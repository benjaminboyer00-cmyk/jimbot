"""Socle commun aux sources : HTTP robuste, cache disque, format unifié."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger("jimbot.data")

# Cache disque court : évite de re-taper les API lors des reruns et des tests.
CACHE_DIR = Path("/tmp/jimbot-cache")
CACHE_TTL = 240  # secondes

USER_AGENT = "jimbot/0.1 (analyse de marché; +https://github.com)"

# Colonnes garanties en sortie de toute source de bougies.
Candles = pd.DataFrame  # index = DatetimeIndex UTC, cols = open/high/low/close/volume


class DataError(RuntimeError):
    """Échec de récupération d'une source, non fatal : l'actif est ignoré."""


def http_get_json(url: str, params: dict | None = None, *, timeout: int = 20,
                  retries: int = 3, cache: bool = True) -> Any:
    """GET JSON avec retry exponentiel et cache disque.

    Les API publiques rate-limitent : on réessaie sur 429/5xx, et on ne fait
    jamais échouer tout le scan pour un actif indisponible.
    """
    key = hashlib.sha256(f"{url}{sorted((params or {}).items())}".encode()).hexdigest()[:20]
    cache_file = CACHE_DIR / f"{key}.json"
    if cache and cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # cache corrompu : on refait l'appel

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                raise DataError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            if cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(payload))
            return payload
        except Exception as e:  # noqa: BLE001 — on retente quelle que soit la cause
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (2 ** attempt))
    raise DataError(f"{url} injoignable après {retries} tentatives : {last_err}")


def normalize(df: pd.DataFrame) -> Candles:
    """Impose le contrat de sortie : colonnes, types, ordre, propreté.

    Toute la suite du moteur suppose ce format ; c'est le seul endroit où on
    tolère de la variabilité entre sources.
    """
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataError(f"colonnes manquantes : {missing}")

    out = df[required].astype("float64").copy()
    out.index = pd.to_datetime(df.index, utc=True)
    out = out[~out.index.duplicated(keep="last")].sort_index()

    # Une bougie sans prix de clôture est inutilisable ; un volume nul est licite.
    out = out.dropna(subset=["close"])
    out["volume"] = out["volume"].fillna(0.0)
    # Cohérence OHLC : certaines API renvoient des high < low sur les bougies creuses.
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    return out[out["close"] > 0]


def fetch_asset(asset, interval: str, limit: int) -> Candles:
    """Dispatch vers la bonne source selon `asset.source`."""
    from . import binance, yahoo  # import tardif : évite un cycle d'import

    if asset.source == "binance":
        return binance.klines(asset.ref, interval, limit)
    if asset.source == "yahoo":
        return yahoo.chart(asset.ref, interval, limit)
    raise DataError(f"source inconnue : {asset.source}")
