"""Données crypto avec chaîne de repli entre fournisseurs.

Motif : `api.binance.com` renvoie **HTTP 451 (indisponible pour raisons
légales)** depuis les runners GitHub, qui tournent sur des adresses IP
américaines géo-bloquées par Binance. Le défaut est invisible en développement
local et supprime en production la totalité des actifs crypto — c'est-à-dire
le cœur du projet.

La parade n'est pas de remplacer Binance par un autre fournisseur unique, qui
créerait le même point de défaillance ailleurs, mais d'essayer plusieurs
sources dans l'ordre jusqu'à obtenir des bougies exploitables :

1. `data-api.binance.vision` — domaine de données publiques de Binance, non
   géo-bloqué, même format d'API et même profondeur d'historique ;
2. `api.binance.com` — l'API principale, qui fonctionne hors des IP bloquées ;
3. Coinbase Exchange — société américaine, donc accessible là où Binance ne
   l'est pas ;
4. Kraken — troisième filet, avec une nomenclature différente (XBT pour BTC).

Chaque fournisseur renvoie le format normalisé commun ; le premier qui répond
gagne, et l'identité du fournisseur retenu est journalisée pour que l'origine
des données reste traçable.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import BROWSER_UA, Candles, DataError, http_get_json, normalize

log = logging.getLogger("jimbot.data.crypto")

# Intervalles internes -> intervalle propre à chaque fournisseur.
BINANCE_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
COINBASE_GRANULARITY = {"15m": 900, "1h": 3600, "4h": 21600, "1d": 86400}
KRAKEN_INTERVALS = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# Kraken conserve une nomenclature historique pour quelques actifs.
KRAKEN_BASE = {"BTC": "XBT", "DOGE": "XDG"}


def _base_of(ref: str) -> str:
    """Extrait la devise de base d'une paire Binance : BTCUSDT -> BTC."""
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if ref.endswith(quote):
            return ref[: -len(quote)]
    return ref


# --------------------------------------------------------------------------
# Fournisseurs
# --------------------------------------------------------------------------
def _binance_like(host: str, ref: str, interval: str, limit: int) -> Candles:
    """API au format Binance : `api.binance.com` et `data-api.binance.vision`."""
    if interval not in BINANCE_INTERVALS:
        raise DataError(f"intervalle non supporté : {interval}")
    raw = http_get_json(f"{host}/api/v3/klines", {
        "symbol": ref,
        "interval": BINANCE_INTERVALS[interval],
        "limit": min(limit, 1000),
    })
    if not isinstance(raw, list) or not raw:
        raise DataError(f"aucune bougie pour {ref}")

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return normalize(df)


def _coinbase(ref: str, interval: str, limit: int) -> Candles:
    """Coinbase Exchange. Attention : l'ordre des colonnes lui est propre."""
    if interval not in COINBASE_GRANULARITY:
        raise DataError(f"intervalle non supporté : {interval}")
    product = f"{_base_of(ref)}-USD"
    raw = http_get_json(
        f"https://api.exchange.coinbase.com/products/{product}/candles",
        {"granularity": COINBASE_GRANULARITY[interval]},
        headers={"User-Agent": BROWSER_UA})
    if not isinstance(raw, list) or not raw:
        raise DataError(f"aucune bougie Coinbase pour {product}")

    # Coinbase renvoie [temps, bas, haut, ouverture, clôture, volume] — un
    # ordre différent de tous les autres — et du plus récent au plus ancien.
    df = pd.DataFrame(raw, columns=["time", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    return normalize(df).tail(limit)


def _kraken(ref: str, interval: str, limit: int) -> Candles:
    """Kraken. Conserve une nomenclature historique : XBT au lieu de BTC."""
    if interval not in KRAKEN_INTERVALS:
        raise DataError(f"intervalle non supporté : {interval}")
    base = _base_of(ref)
    pair = f"{KRAKEN_BASE.get(base, base)}USD"
    raw = http_get_json("https://api.kraken.com/0/public/OHLC",
                        {"pair": pair, "interval": KRAKEN_INTERVALS[interval]},
                        headers={"User-Agent": BROWSER_UA})
    if not isinstance(raw, dict) or raw.get("error"):
        raise DataError(f"Kraken : {(raw or {}).get('error')}")

    result = raw.get("result") or {}
    # La clé de résultat n'est pas la paire demandée : Kraken renvoie son
    # propre identifiant interne (XXBTZUSD pour XBTUSD).
    rows = next((v for k, v in result.items() if k != "last"), None)
    if not rows:
        raise DataError(f"aucune bougie Kraken pour {pair}")

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close",
                                     "vwap", "volume", "count"])
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    return normalize(df).tail(limit)


# Ordre d'essai. Le domaine de données publiques passe en premier parce que
# c'est le seul qui soit à la fois non géo-bloqué et au format Binance.
PROVIDERS: list[tuple[str, callable]] = [
    ("binance.vision", lambda ref, itv, lim: _binance_like(
        "https://data-api.binance.vision", ref, itv, lim)),
    ("binance", lambda ref, itv, lim: _binance_like(
        "https://api.binance.com", ref, itv, lim)),
    ("coinbase", _coinbase),
    ("kraken", _kraken),
]

# Nombre minimal de bougies pour qu'une réponse soit jugée exploitable.
MIN_CANDLES = 60


def klines(ref: str, interval: str = "1h", limit: int = 400) -> Candles:
    """Bougies OHLCV, en essayant les fournisseurs dans l'ordre."""
    erreurs: list[str] = []
    for nom, fetch in PROVIDERS:
        try:
            df = fetch(ref, interval, limit)
        except DataError as e:
            erreurs.append(f"{nom}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — un fournisseur cassé ne doit pas tout arrêter
            erreurs.append(f"{nom}: {type(e).__name__} {e}")
            continue

        if len(df) < MIN_CANDLES:
            # Coinbase plafonne à 300 bougies par requête et Kraken à 720 :
            # une réponse courte est normale, une réponse quasi vide ne l'est pas.
            erreurs.append(f"{nom}: seulement {len(df)} bougies")
            continue

        log.debug("%s %s : %d bougies via %s", ref, interval, len(df), nom)
        df.attrs["provider"] = nom
        return df

    raise DataError(f"{ref} indisponible chez tous les fournisseurs — "
                    + " | ".join(erreurs))


def ticker_24h(ref: str) -> dict:
    """Statistiques glissantes 24 h, via le premier fournisseur disponible."""
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            d = http_get_json(f"{host}/api/v3/ticker/24hr", {"symbol": ref})
            return {
                "change_pct": float(d.get("priceChangePercent", 0.0)),
                "quote_volume": float(d.get("quoteVolume", 0.0)),
                "high": float(d.get("highPrice", 0.0)),
                "low": float(d.get("lowPrice", 0.0)),
                "trades": int(d.get("count", 0)),
            }
        except (DataError, ValueError, TypeError):
            continue
    raise DataError(f"statistiques 24 h indisponibles pour {ref}")
