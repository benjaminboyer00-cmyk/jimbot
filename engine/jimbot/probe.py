"""Mesure du pouvoir prédictif de chaque facteur, pris isolément.

Le backtest répond à « le système gagne-t-il ? ». Ce module répond à la
question qui vient avant : « qu'est-ce qui, dans ce système, prédit quoi que
ce soit ? »

La distinction est méthodologique. Le backtest n'observe que les trades ayant
franchi le seuil, soit un échantillon doublement sélectionné — par le score et
par le filtre d'espérance. On ne peut rien en conclure sur la valeur des
facteurs pris individuellement.

La sonde procède autrement : à chaque pas, elle enregistre la valeur de tous
les facteurs **sans aucun filtre**, puis le rendement effectivement réalisé
sur plusieurs horizons, normalisé par l'ATR pour être comparable d'un actif à
l'autre. La corrélation entre les deux — le coefficient d'information — dit si
le facteur porte de l'information.

Ordres de grandeur admis en gestion quantitative : un IC de 0.02 à 0.05 est
déjà exploitable sur un grand nombre de paris ; au-delà de 0.10 c'est
inhabituel ; un IC nul signifie que le facteur est du bruit.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import indicators as I
from . import stats as S
from .config import Asset
from .strategy import (factor_breakout, factor_momentum, factor_mean_reversion,
                       factor_structure, factor_trend, factor_volume)

log = logging.getLogger("jimbot.probe")

WINDOW = 400

# Horizons de mesure, en bougies. Un facteur peut porter de l'information à
# court terme et n'en avoir aucune à moyen terme — c'est précisément ce qu'on
# veut savoir pour régler l'horizon d'avantage.
HORIZONS = (6, 12, 24, 48)

# Le sentiment est exclu : il n'est pas reconstituable sur l'historique.
FACTORS = {
    "trend": factor_trend,
    "momentum": factor_momentum,
    "mean_reversion": factor_mean_reversion,
    "volume": factor_volume,
    "breakout": factor_breakout,
    "structure": factor_structure,
}


def probe_asset(asset: Asset, df: pd.DataFrame, *, step: int = 4,
                window: int = WINDOW) -> list[dict]:
    """Enregistre facteurs et rendements futurs, à chaque pas, sans filtre."""
    rows: list[dict] = []
    n = len(df)
    horizon_max = max(HORIZONS)
    if n < window + horizon_max + 10:
        return rows

    close = df["close"].to_numpy(dtype=float)

    for i in range(window, n - horizon_max, step):
        past = df.iloc[i - window: i + 1]
        try:
            regime = S.detect_regime(past)
            valeurs = {nom: fn(past).value for nom, fn in FACTORS.items()}
            atr_v = S._last(I.atr(past["high"], past["low"], past["close"]))
        except Exception as e:  # noqa: BLE001
            log.debug("%s @%d : %s", asset.symbol, i, e)
            continue

        if not np.isfinite(atr_v) or atr_v <= 0:
            continue

        prix = close[i]
        ligne = {"symbol": asset.symbol, "klass": asset.klass,
                 "regime": regime.name, "quality": regime.quality,
                 "index": i, **valeurs}
        # Rendement futur en unités d'ATR : comparable entre actifs de
        # volatilités très différentes.
        for h in HORIZONS:
            ligne[f"fwd_{h}"] = float((close[i + h] - prix) / atr_v)
        rows.append(ligne)

    log.info("%s : %d observation(s)", asset.symbol, len(rows))
    return rows


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------
def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Corrélation de rang, sans dépendance à scipy.

    Spearman n'est rien d'autre qu'un Pearson calculé sur les rangs :
    l'implémenter ainsi évite d'ajouter une dépendance lourde à un projet qui
    tourne dans un runner CI.
    """
    if len(a) < 3:
        return float("nan")
    return float(a.rank().corr(b.rank(), method="pearson"))


def information_coefficients(rows: list[dict]) -> dict:
    """Corrélation de chaque facteur avec le rendement futur.

    On utilise la corrélation de rang (Spearman) plutôt que de Pearson :
    elle ne suppose aucune relation linéaire et résiste aux valeurs extrêmes,
    dont les marchés sont pleins.
    """
    if len(rows) < 100:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(rows)
    out: dict = {"observations": len(df), "par_facteur": {}}

    for nom in FACTORS:
        if nom not in df.columns:
            continue
        entree = {}
        for h in HORIZONS:
            col = f"fwd_{h}"
            sub = df[[nom, col]].dropna()
            # Un facteur constant n'a pas de rang : la corrélation serait NaN.
            if len(sub) < 50 or sub[nom].nunique() < 5:
                continue
            ic = _spearman(sub[nom], sub[col])
            if not np.isfinite(ic):
                continue
            # t de Student approché pour une corrélation de rang.
            t = ic * np.sqrt((len(sub) - 2) / max(1e-9, 1 - ic ** 2))
            entree[f"h{h}"] = {"ic": round(ic, 4), "t": round(float(t), 2),
                               "n": len(sub), "significatif": bool(abs(t) > 2.0)}
        if entree:
            meilleur = max(entree.items(), key=lambda kv: abs(kv[1]["ic"]))
            out["par_facteur"][nom] = {
                "horizons": entree,
                "meilleur_horizon": meilleur[0],
                "ic_max": meilleur[1]["ic"],
                "significatif": meilleur[1]["significatif"],
            }

    return out


def ic_by_regime(rows: list[dict], horizon: int = 24) -> dict:
    """Le pouvoir prédictif dépend-il du régime ?

    C'est l'hypothèse fondatrice du moteur — que les pondérations doivent
    changer selon le régime. Elle n'avait jamais été vérifiée.
    """
    if len(rows) < 200:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(rows)
    col = f"fwd_{horizon}"
    out: dict = {}

    for regime, sub in df.groupby("regime"):
        if len(sub) < 80:
            continue
        entree = {"observations": len(sub)}
        for nom in FACTORS:
            if nom not in sub.columns or sub[nom].nunique() < 5:
                continue
            paire = sub[[nom, col]].dropna()
            if len(paire) < 50:
                continue
            ic = _spearman(paire[nom], paire[col])
            if np.isfinite(ic):
                t = ic * np.sqrt((len(paire) - 2) / max(1e-9, 1 - ic ** 2))
                entree[nom] = {"ic": round(ic, 4), "t": round(float(t), 2)}
        out[regime] = entree
    return out


def derived_weights(ic_regime: dict, plancher: float = 0.02) -> dict:
    """Déduit des pondérations des coefficients mesurés.

    Principe : le poids d'un facteur doit être proportionnel à l'information
    qu'il porte, et son signe doit suivre celui de sa corrélation. Un facteur
    dont l'IC est négatif prédit à l'envers — le bon usage est alors de
    l'inverser, pas de l'ignorer.

    Seuls les coefficients statistiquement distinguables du bruit sont
    retenus ; les autres reçoivent le plancher, faute de quoi on ajusterait
    les pondérations sur du hasard.
    """
    out: dict = {}
    for regime, mesures in ic_regime.items():
        bruts = {}
        for nom, v in mesures.items():
            if nom == "observations" or not isinstance(v, dict):
                continue
            # |t| > 2 : le coefficient se distingue du bruit.
            bruts[nom] = v["ic"] if abs(v.get("t", 0)) > 2.0 else 0.0

        total = sum(abs(x) for x in bruts.values())
        if total <= 0:
            # Aucun facteur ne ressort : on répartit uniformément plutôt que
            # de prétendre à une hiérarchie.
            n = max(len(bruts), 1)
            out[regime] = {nom: round(1.0 / n, 3) for nom in bruts}
            out[regime]["_note"] = "aucun facteur significatif, répartition uniforme"
            continue

        poids = {}
        for nom, ic in bruts.items():
            p = ic / total
            # Plancher signé : un facteur non significatif garde une présence
            # minimale, sans peser sur la décision.
            poids[nom] = round(p if abs(p) >= plancher else 0.0, 4)
        out[regime] = poids
    return out
