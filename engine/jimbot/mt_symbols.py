"""Correspondance entre les symboles internes et ceux de MetaTrader.

Il n'existe aucune nomenclature standard : le S&P 500 est « US500 » chez un
courtier, « SPX500 » ou « USA500 » chez un autre, et les paires portent souvent
un suffixe (« .r », « m », « _i »). On expose donc plusieurs alias par
instrument et l'on retient celui que le courtier reconnaît.

La table vit ici, du côté du moteur, parce que c'est lui qui passe désormais
les ordres. Elle est publiée dans l'instantané pour que le site et l'API la
lisent au lieu d'en tenir une copie : deux tables d'alias auraient divergé au
premier courtier ajouté, et un ordre serait parti sur le mauvais instrument.
"""
from __future__ import annotations

MT_ALIASES: dict[str, list[str]] = {
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.r", "XAUUSDm"],
    "EURUSD": ["EURUSD", "EURUSD.r", "EURUSDm"],
    "GBPUSD": ["GBPUSD", "GBPUSD.r", "GBPUSDm"],
    "USDJPY": ["USDJPY", "USDJPY.r", "USDJPYm"],
    "SPX": ["US500", "SPX500", "USA500", "S&P500"],
    "NDX": ["NAS100", "USTEC", "NDX100", "USATEC"],
    "DXY": ["USDX", "DXY", "USDOLLAR"],
    "VIX": ["VIX", "VOLX"],
    "BTC-USD": ["BTCUSD", "BTCUSDT", "BTCUSD.r"],
    "ETH-USD": ["ETHUSD", "ETHUSDT"],
    "SOL-USD": ["SOLUSD", "SOLUSDT"],
    "BNB-USD": ["BNBUSD", "BNBUSDT"],
    "XRP-USD": ["XRPUSD", "XRPUSDT"],
    "DOGE-USD": ["DOGEUSD", "DOGEUSDT"],
    "AVAX-USD": ["AVAXUSD", "AVAXUSDT"],
    "LINK-USD": ["LINKUSD", "LINKUSDT"],
}


def aliases(symbol: str) -> list[str]:
    """Alias connus d'un instrument, du plus courant au plus rare."""
    return MT_ALIASES.get(symbol, [symbol.replace("-", "")])


def principal(symbol: str) -> str:
    """Alias principal, celui que la plupart des courtiers reconnaissent."""
    return aliases(symbol)[0]
