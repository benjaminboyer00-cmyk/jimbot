"""Crypto majeures via l'API publique Binance (aucune clé requise)."""
from __future__ import annotations

import pandas as pd

from .base import Candles, DataError, http_get_json, normalize

BASE = "https://api.binance.com/api/v3"

# Intervalles internes -> intervalles Binance.
INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


def klines(symbol: str, interval: str = "1h", limit: int = 400) -> Candles:
    """Bougies OHLCV. `symbol` est une paire Binance, ex. "BTCUSDT"."""
    if interval not in INTERVALS:
        raise DataError(f"intervalle non supporté : {interval}")
    raw = http_get_json(f"{BASE}/klines", {
        "symbol": symbol,
        "interval": INTERVALS[interval],
        "limit": min(limit, 1000),
    })
    if not isinstance(raw, list) or not raw:
        raise DataError(f"aucune bougie pour {symbol}")

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return normalize(df)


def ticker_24h(symbol: str) -> dict:
    """Statistiques glissantes 24 h : variation, volume, plus-haut/plus-bas."""
    d = http_get_json(f"{BASE}/ticker/24hr", {"symbol": symbol})
    return {
        "change_pct": float(d.get("priceChangePercent", 0.0)),
        "quote_volume": float(d.get("quoteVolume", 0.0)),
        "high": float(d.get("highPrice", 0.0)),
        "low": float(d.get("lowPrice", 0.0)),
        "trades": int(d.get("count", 0)),
    }
