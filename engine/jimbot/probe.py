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
                       factor_structure, factor_trend, factor_volume, htf_bias)

log = logging.getLogger("jimbot.probe")

WINDOW = 400

# Horizons de mesure, en bougies. Un facteur peut porter de l'information à
# court terme et n'en avoir aucune à moyen terme — c'est précisément ce qu'on
# veut savoir pour régler l'horizon d'avantage.
#
# Ils sont propres à chaque unité de temps : 48 bougies représentent deux jours
# en horaire et deux mois en journalier. Comparer les deux sans les adapter
# n'aurait aucun sens. Les valeurs retenues couvrent, dans chaque cas, de
# quelques heures à quelques semaines de marché.
HORIZONS_PAR_INTERVALLE: dict[str, tuple[int, ...]] = {
    # 5 min : de un quart d'heure à deux heures. Au-delà, l'horizon n'a plus
    # rien de scalping et l'unité horaire est mieux placée pour le mesurer.
    "5m": (3, 6, 12, 24),
    "15m": (8, 24, 48, 96),
    "1h": (6, 12, 24, 48),
    "4h": (3, 6, 12, 30),
    "1d": (2, 5, 10, 20),
}
HORIZONS = HORIZONS_PAR_INTERVALLE["1h"]

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
                window: int = WINDOW,
                horizons: tuple[int, ...] | None = None) -> list[dict]:
    """Enregistre facteurs et rendements futurs, à chaque pas, sans filtre."""
    horizons = horizons or HORIZONS
    rows: list[dict] = []
    n = len(df)
    horizon_max = max(horizons)
    if n < window + horizon_max + 10:
        return rows

    close = df["close"].to_numpy(dtype=float)

    for i in range(window, n - horizon_max, step):
        past = df.iloc[i - window: i + 1]
        try:
            regime = S.detect_regime(past)
            valeurs = {nom: fn(past).value for nom, fn in FACTORS.items()}
            # Le biais d'unité supérieure sert de porte au score : il faut
            # savoir s'il prédit dans le bon sens, faute de quoi il pénalise
            # précisément les signaux corrects. On l'approxime en agrégeant la
            # fenêtre passée par quatre, ce que fait la production en chargeant
            # une granularité supérieure.
            htf = past.resample("4h").agg(
                {"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"}).dropna(subset=["close"])
            valeurs["htf"] = htf_bias(htf)[0] if len(htf) >= 60 else 0.0
            atr_v = S._last(I.atr(past["high"], past["low"], past["close"]))
        except Exception as e:  # noqa: BLE001
            log.debug("%s @%d : %s", asset.symbol, i, e)
            continue

        if not np.isfinite(atr_v) or atr_v <= 0:
            continue

        prix = close[i]
        # L'horodatage est indispensable à la validation hors échantillon :
        # les actifs ne couvrent pas les mêmes périodes, et un découpage par
        # position mélangerait des époques différentes d'un actif à l'autre.
        ligne = {"symbol": asset.symbol, "klass": asset.klass,
                 "regime": regime.name, "quality": regime.quality,
                 "index": i, "t": df.index[i].isoformat(), **valeurs}
        # Deux mesures du même rendement futur, parce qu'elles répondent à deux
        # questions différentes. En unités d'ATR, il est comparable entre actifs
        # de volatilités très différentes — c'est ce qu'il faut pour un
        # coefficient d'information. En pourcentage, il se compare au spread et
        # aux frais, qui sont eux aussi des pourcentages : c'est la seule forme
        # dans laquelle « l'avantage survit-il aux coûts ? » a un sens.
        ligne["atr_pct"] = float(atr_v / prix * 100.0) if prix > 0 else float("nan")
        for h in horizons:
            ligne[f"fwd_{h}"] = float((close[i + h] - prix) / atr_v)
            ligne[f"pct_{h}"] = float((close[i + h] / prix - 1.0) * 100.0) if prix > 0 else float("nan")
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


def information_coefficients(rows: list[dict],
                            horizons: tuple[int, ...] | None = None) -> dict:
    """Corrélation de chaque facteur avec le rendement futur.

    On utilise la corrélation de rang (Spearman) plutôt que de Pearson :
    elle ne suppose aucune relation linéaire et résiste aux valeurs extrêmes,
    dont les marchés sont pleins.
    """
    if len(rows) < 100:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(rows)
    horizons = horizons or tuple(
        int(c.removeprefix("fwd_")) for c in df.columns if c.startswith("fwd_"))
    out: dict = {"observations": len(df), "horizons": list(horizons),
                 "par_facteur": {}}

    for nom in FACTORS:
        if nom not in df.columns:
            continue
        entree = {}
        for h in horizons:
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


# --------------------------------------------------------------------------
# L'avantage survit-il aux coûts ?
# --------------------------------------------------------------------------
# Coût d'un aller-retour, en points de base du notionnel (1 pb = 0,01 %).
#
# Ces chiffres décident de tout à horizon court, et ils ne sont pas négociables
# par le modèle : ils sont fixés par le courtier. Un scalping qui gagne 3 pb
# bruts et paie 20 pb de frais perd, quelle que soit la qualité du signal.
#
# Les valeurs retenues sont celles d'un compte particulier ordinaire, sans
# remise de volume :
#   - binance_taker : 10 pb par côté, soit 20 pb l'aller-retour. C'est le tarif
#     par défaut de Binance spot, celui qu'on paie en franchissant le carnet.
#   - binance_maker : 10 pb par côté également au tarif de base, mais posé dans
#     le carnet — on n'y ajoute pas le spread puisqu'on le fabrique.
#   - cfd_serre : un CFD crypto chez un courtier retail, sans commission mais
#     avec un spread de l'ordre de 5 pb par côté sur BTC en séance liquide.
#   - parfait : aucun coût. Ne décrit aucun compte réel ; sert de borne haute,
#     pour distinguer « le signal ne vaut rien » de « le signal vaut quelque
#     chose mais moins que le péage ».
COUTS_ALLER_RETOUR_PB: dict[str, float] = {
    "parfait": 0.0,
    "binance_maker": 20.0,
    "cfd_serre": 10.0,
    "binance_taker": 20.0,
}


def edge_net(rows: list[dict], score_col: str = "score",
             horizons: tuple[int, ...] | None = None,
             quantile: float = 0.2) -> dict:
    """Rendement d'une stratégie de quintile, brut puis net de coûts.

    Le coefficient d'information dit si un facteur porte de l'information. Il
    ne dit pas si on peut en vivre : un IC de 0,05 sur un rendement dont
    l'écart-type vaut 3 points de base ne paiera jamais 20 points de base de
    frais. À horizon court, c'est cette seconde question qui tranche, et elle
    se pose en pourcentages du notionnel — pas en unités d'ATR.

    Le protocole est celui qu'on appliquerait à la main : à chaque pas, on
    prend une position longue sur le quintile le mieux noté, courte sur le
    moins bien noté, on la tient `h` bougies, et on paie l'aller-retour. La
    moyenne des deux jambes est l'avantage brut par pari ; on en retranche le
    coût, et le t de Student dit si ce qui reste se distingue de zéro.
    """
    if len(rows) < 200:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(rows)
    if score_col not in df.columns:
        return {"note": f"colonne « {score_col} » absente"}

    horizons = horizons or tuple(
        int(c.removeprefix("pct_")) for c in df.columns if c.startswith("pct_"))

    out: dict = {"observations": len(df), "quantile": quantile,
                 "couts_pb": COUTS_ALLER_RETOUR_PB, "par_horizon": {}}

    for h in horizons:
        col = f"pct_{h}"
        if col not in df.columns:
            continue
        sub = df[[score_col, col]].dropna()
        if len(sub) < 200 or sub[score_col].nunique() < 10:
            continue

        haut = sub[score_col].quantile(1 - quantile)
        bas = sub[score_col].quantile(quantile)
        longs = sub.loc[sub[score_col] >= haut, col]
        courts = sub.loc[sub[score_col] <= bas, col]
        if len(longs) < 50 or len(courts) < 50:
            continue

        # La jambe courte gagne quand le prix baisse : on retourne son signe
        # pour que les deux jambes se moyennent comme deux paris de même sens.
        paris = pd.concat([longs, -courts])
        brut_pb = float(paris.mean()) * 100.0          # % -> points de base
        ecart_pb = float(paris.std()) * 100.0
        n = len(paris)
        t_brut = brut_pb / (ecart_pb / np.sqrt(n)) if ecart_pb > 0 else float("nan")

        entree = {
            "n_paris": n,
            "brut_pb": round(brut_pb, 2),
            "ecart_type_pb": round(ecart_pb, 1),
            "t_brut": round(float(t_brut), 2) if np.isfinite(t_brut) else None,
            "significatif_brut": bool(np.isfinite(t_brut) and abs(t_brut) > 2.0),
            "net_pb": {},
        }
        for nom, cout in COUTS_ALLER_RETOUR_PB.items():
            net = brut_pb - cout
            t_net = net / (ecart_pb / np.sqrt(n)) if ecart_pb > 0 else float("nan")
            entree["net_pb"][nom] = {
                "net_pb": round(net, 2),
                "t": round(float(t_net), 2) if np.isfinite(t_net) else None,
                "rentable": bool(np.isfinite(t_net) and net > 0 and t_net > 2.0),
            }
        out["par_horizon"][f"h{h}"] = entree

    # Le coût que l'avantage brut permettrait de payer : c'est le chiffre à
    # confronter au tarif d'un courtier avant d'ouvrir un compte.
    meilleurs = [(h, e["brut_pb"]) for h, e in out["par_horizon"].items()]
    if meilleurs:
        h, brut = max(meilleurs, key=lambda kv: kv[1])
        out["meilleur_horizon"] = h
        out["cout_maximal_supportable_pb"] = round(brut, 2)

    return out


def score_combine(rows: list[dict], poids: dict[str, float]) -> list[dict]:
    """Ajoute à chaque observation le score que produirait la production.

    Mesurer les facteurs un par un ne dit pas ce que vaut leur combinaison :
    c'est pourtant elle qu'on trade. Les poids sont passés explicitement plutôt
    que lus dans `strategy`, pour qu'on puisse mesurer une pondération
    candidate sans la mettre en production d'abord.
    """
    norme = sum(abs(p) for p in poids.values()) or 1.0
    for r in rows:
        somme = sum(r.get(nom, 0.0) * poids.get(nom, 0.0) for nom in poids)
        r["score"] = somme / norme
    return rows
