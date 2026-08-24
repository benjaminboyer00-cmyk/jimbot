"""Forex, indices et actions via l'endpoint chart public de Yahoo Finance.

Pas de clé d'API, mais un rate-limit implicite : le cache disque de `base`
et le nombre restreint de symboles suivis suffisent à rester sous le radar.
"""
from __future__ import annotations

import pandas as pd

from .base import Candles, DataError, http_get_json, normalize

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo impose une profondeur maximale par granularité : les bougies
# intraday fines ne remontent pas au-delà de quelques jours.
RANGE_FOR = {"15m": "60d", "1h": "730d", "1d": "5y"}
INTERVALS = {"15m": "15m", "1h": "1h", "1d": "1d"}


def chart(symbol: str, interval: str = "1h", limit: int = 400) -> Candles:
    """Bougies OHLCV. `symbol` est un ticker Yahoo, ex. "EURUSD=X", "^GSPC"."""
    # 4h n'existe pas chez Yahoo : on retombe sur 1h, l'agrégation se fera plus haut.
    yf_interval = INTERVALS.get(interval, "1h")
    raw = http_get_json(f"{BASE}/{symbol}", {
        "interval": yf_interval,
        "range": RANGE_FOR.get(yf_interval, "730d"),
        "includePrePost": "false",
    })

    try:
        result = raw["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        stamps = result["timestamp"]
    except (KeyError, IndexError, TypeError) as e:
        err = (raw or {}).get("chart", {}).get("error") if isinstance(raw, dict) else None
        raise DataError(f"réponse Yahoo inexploitable pour {symbol} ({err or e})") from e

    df = pd.DataFrame({
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
    })
    df.index = pd.to_datetime(stamps, unit="s", utc=True)
    # Yahoo renvoie des trous (jours fériés, séances écourtées) sous forme de null.
    df = df.dropna(subset=["close"])
    out = normalize(df)
    if len(out) < 30:
        raise DataError(f"historique trop court pour {symbol} ({len(out)} bougies)")
    return out.tail(limit)


def resample_4h(df: Candles) -> Candles:
    """Agrège des bougies 1 h en 4 h (Yahoo ne propose pas cette granularité)."""
    return df.resample("4h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])
