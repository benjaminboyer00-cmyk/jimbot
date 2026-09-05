"""Données de positionnement, publiées par Binance Futures.

Le prix dit où le marché est allé. Ces séries disent *comment il y est allé* :
qui est positionné, dans quel sens, avec quel levier, et ce que ça leur coûte.
C'est une information que l'OHLCV ne contient pas, et c'est la seule façon
librement mesurable d'approcher ce que font les gros intervenants.

Toutes ces sources sont gratuites et sans clé. Leur limite est ailleurs, et
elle commande tout ce qu'on peut en faire :

    taux de financement      166 jours    mesurable par la sonde
    peur et cupidité       3 135 jours    mesurable, mais quotidien
    intérêt ouvert            21 jours    trop court
    ratio des gros comptes    21 jours    trop court

Binance ne conserve que trente jours des trois dernières. On ne peut pas
mesurer un pouvoir prédictif sur vingt jours : la seule chose honnête à faire
est de commencer à les enregistrer maintenant, pour pouvoir les mesurer dans
quelques mois. C'est exactement ce que `history.py` fait déjà pour les prix.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import DataError, http_get_json

log = logging.getLogger("jimbot.futures")

FAPI = "https://fapi.binance.com"

# Correspondance vers les paires perpétuelles. Toutes les cryptos de l'univers
# y sont cotées ; le forex et les indices n'ont évidemment rien ici.
PERPETUELS: dict[str, str] = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT", "DOGE-USD": "DOGEUSDT",
    "AVAX-USD": "AVAXUSDT", "LINK-USD": "LINKUSDT",
}


def perpetuel(symbole: str) -> str | None:
    return PERPETUELS.get(symbole)


def _serie(rows: list[dict], cle_temps: str, cle_valeur: str) -> pd.Series:
    if not rows:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime([int(r[cle_temps]) for r in rows], unit="ms", utc=True)
    vals = [float(r[cle_valeur]) for r in rows]
    s = pd.Series(vals, index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


def taux_financement(ref: str, limit: int = 1000) -> pd.Series:
    """Taux de financement des perpétuels, par pas de huit heures.

    Ce que les positions longues paient aux courtes, ou l'inverse. Un taux
    fortement positif signifie que les acheteurs à levier dominent et paient
    pour le rester : c'est une mesure d'encombrement du côté long, et c'est
    la seule des quatre séries à porter un historique exploitable.
    """
    try:
        rows = http_get_json(f"{FAPI}/fapi/v1/fundingRate",
                             {"symbol": ref, "limit": min(limit, 1000)})
    except DataError as e:
        log.warning("financement %s : %s", ref, e)
        return pd.Series(dtype="float64")
    return _serie(rows, "fundingTime", "fundingRate")


def interet_ouvert(ref: str, periode: str = "1h", limit: int = 500) -> pd.Series:
    """Somme des positions ouvertes. Vingt jours d'historique au maximum."""
    try:
        rows = http_get_json(f"{FAPI}/futures/data/openInterestHist",
                             {"symbol": ref, "period": periode,
                              "limit": min(limit, 500)})
    except DataError as e:
        log.warning("intérêt ouvert %s : %s", ref, e)
        return pd.Series(dtype="float64")
    return _serie(rows, "timestamp", "sumOpenInterest")


def ratio_gros_comptes(ref: str, periode: str = "1h", limit: int = 500) -> pd.Series:
    """Part longue des positions des plus gros comptes de Binance.

    C'est le plus proche substitut public à « ce que font les whales » : non
    pas des mouvements de portefeuille, qui peuvent n'être que des transferts
    internes, mais du positionnement effectif à levier.

    Vingt jours d'historique : à enregistrer, pas encore à mesurer.
    """
    try:
        rows = http_get_json(f"{FAPI}/futures/data/topLongShortPositionRatio",
                             {"symbol": ref, "period": periode,
                              "limit": min(limit, 500)})
    except DataError as e:
        log.warning("ratio gros comptes %s : %s", ref, e)
        return pd.Series(dtype="float64")
    return _serie(rows, "timestamp", "longAccount")


def ratio_comptes(ref: str, periode: str = "1h", limit: int = 500) -> pd.Series:
    """Part longue de l'ensemble des comptes. Le contrepoint du précédent :
    l'écart entre les deux dit si les gros et la foule sont d'accord."""
    try:
        rows = http_get_json(f"{FAPI}/futures/data/globalLongShortAccountRatio",
                             {"symbol": ref, "period": periode,
                              "limit": min(limit, 500)})
    except DataError as e:
        log.warning("ratio comptes %s : %s", ref, e)
        return pd.Series(dtype="float64")
    return _serie(rows, "timestamp", "longAccount")


def peur_cupidite(limit: int = 0) -> pd.Series:
    """Indice de peur et cupidité, quotidien, 3 135 jours d'historique.

    Quotidien : il ne peut pas discriminer à l'horizon horaire du moteur, mais
    c'est la seule série de sentiment reconstituable sur des années.
    """
    try:
        d = http_get_json("https://api.alternative.me/fng/", {"limit": limit})
    except DataError as e:
        log.warning("peur et cupidité : %s", e)
        return pd.Series(dtype="float64")
    rows = d.get("data") or []
    if not rows:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime([int(r["timestamp"]) for r in rows], unit="s", utc=True)
    s = pd.Series([float(r["value"]) for r in rows], index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


def aligner(serie: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Reporte une série sur l'index des bougies, sans jamais regarder devant.

    `reindex(method="ffill")` propage la dernière valeur *connue à cette date*.
    Une interpolation, elle, mélangerait la valeur suivante dans le passé — et
    donnerait à la sonde une information que le moteur n'aurait pas eue au
    moment de décider. C'est la faute qui rend un backtest brillant et une
    exécution décevante.
    """
    if serie.empty:
        return pd.Series(index=index, dtype="float64")
    return serie.reindex(index, method="ffill")


def enrichir(df: pd.DataFrame, symbole: str, *, complet: bool = False) -> pd.DataFrame:
    """Ajoute les colonnes de positionnement aux bougies d'un actif.

    Ne fait rien pour un actif sans perpétuel — le forex, les indices et les
    actions n'ont pas de taux de financement, et le contrat de sortie ne peut
    pas exiger des colonnes que la moitié de l'univers ne peut pas fournir.

    `complet` ajoute les séries à historique court (intérêt ouvert, ratios de
    comptes). Elles ne couvrent que vingt jours : utiles à afficher et à
    enregistrer, inutilisables pour mesurer un pouvoir prédictif, et les mêler
    aux autres ferait chuter l'échantillon de la sonde à la plus courte.
    """
    ref = perpetuel(symbole)
    if ref is None or df.empty:
        return df

    out = df.copy()
    out["financement"] = aligner(taux_financement(ref), out.index)

    if complet:
        out["interet_ouvert"] = aligner(interet_ouvert(ref), out.index)
        out["ratio_gros"] = aligner(ratio_gros_comptes(ref), out.index)
        out["ratio_foule"] = aligner(ratio_comptes(ref), out.index)
    return out
