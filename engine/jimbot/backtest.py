"""Validation walk-forward du moteur de signal.

Tout l'édifice repose sur une hypothèse jamais vérifiée : qu'un score élevé
corresponde à un avantage réel. Ce module la met à l'épreuve.

Le principe est simple et strict : on avance bougie par bougie, on n'analyse
que les données disponibles à cet instant, et on laisse le marché trancher sur
les bougies suivantes. Aucune information future ne peut entrer dans la
décision — c'est la seule façon d'obtenir un chiffre qui veuille dire quelque
chose.

Deux limites à garder en tête :

1. **Pas d'actualité historique.** On ne peut pas reconstituer le flux de
   presse d'il y a deux mois. Le facteur de sentiment est donc neutralisé, et
   le backtest mesure la partie technique seule. C'est une sous-estimation si
   le sentiment aide, une surestimation s'il nuit.

2. **Pas de données infra-bougie.** Quand une bougie touche le stop et
   l'objectif, on retient le stop, comme dans le portefeuille papier.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

from .config import Asset
from .paper import _cost_bps
from .strategy import analyze

log = logging.getLogger("jimbot.backtest")

# Fenêtre d'analyse : autant de bougies que le moteur en reçoit en production,
# sinon on ne mesurerait pas le même système.
WINDOW = 400

# Pas d'avancement, en bougies. Analyser chaque bougie produirait des signaux
# quasi identiques et corrélés, sans rien apporter à la mesure.
STEP = 4

# Durée maximale d'un trade, comme dans le portefeuille papier.
MAX_HOLD = 120


@dataclass
class BacktestTrade:
    """Un trade simulé, avec son issue déterminée par le marché."""

    symbol: str
    klass: str
    direction: str
    score: float
    win_prob: float          # probabilité prédite par le modèle
    expected_r: float
    rr: float
    regime: str
    entry: float
    stop: float
    target: float
    exit: float
    outcome: str             # "cible" | "stop" | "expiration"
    r_multiple: float
    bars_held: int
    mfe: float
    mae: float
    stop_atr: float
    stop_basis: str          # niveau structurel ayant justifié le stop
    target_basis: str
    stop_strength: float     # solidité du niveau adossant le stop
    obstacle: float          # résistance cumulée entre le prix et l'objectif
    index: int               # position de l'entrée dans la série

    def to_dict(self) -> dict:
        return asdict(self)


def simulate_exit(future: pd.DataFrame, direction: str, entry: float,
                  stop: float, target: float, klass: str) -> tuple[str, float, int, float, float]:
    """Fait vivre le trade sur les bougies suivantes.

    Renvoie (issue, prix de sortie, bougies tenues, MFE en R, MAE en R).
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return "invalide", entry, 0, 0.0, 0.0

    sign = 1.0 if direction == "long" else -1.0
    mfe = mae = 0.0

    for i, (_, bar) in enumerate(future.iterrows(), start=1):
        hi, lo = float(bar["high"]), float(bar["low"])
        best = hi if direction == "long" else lo
        worst = lo if direction == "long" else hi
        mfe = max(mfe, sign * (best - entry) / risk)
        mae = min(mae, sign * (worst - entry) / risk)

        if direction == "long":
            touche_stop, touche_cible = lo <= stop, hi >= target
        else:
            touche_stop, touche_cible = hi >= stop, lo <= target

        # Le stop prime en cas d'ambiguïté : sans données infra-bougie,
        # supposer l'issue favorable reviendrait à truquer le résultat.
        if touche_stop:
            return "stop", stop, i, round(mfe, 2), round(mae, 2)
        if touche_cible:
            return "cible", target, i, round(mfe, 2), round(mae, 2)
        if i >= MAX_HOLD:
            return "expiration", float(bar["close"]), i, round(mfe, 2), round(mae, 2)

    if future.empty:
        return "tronque", entry, 0, 0.0, 0.0
    return "tronque", float(future["close"].iloc[-1]), len(future), round(mfe, 2), round(mae, 2)


def run_asset(asset: Asset, df: pd.DataFrame, *, step: int = STEP,
              window: int = WINDOW) -> list[dict]:
    """Walk-forward sur un actif. Renvoie les trades simulés."""
    trades: list[dict] = []
    n = len(df)
    if n < window + 40:
        log.warning("%s : historique trop court (%d bougies)", asset.symbol, n)
        return trades

    bps = _cost_bps(asset.klass) / 2.0 / 10_000.0

    for i in range(window, n - 1, step):
        # Fenêtre strictement passée : la bougie i est la dernière connue.
        past = df.iloc[i - window: i + 1]
        try:
            sig = analyze(asset, past, timeframe="1h", news_score=0.0, news_count=0)
        except Exception as e:  # noqa: BLE001
            log.debug("%s @%d : analyse impossible (%s)", asset.symbol, i, e)
            continue

        if not sig.actionable or sig.stop <= 0:
            continue

        # Coûts d'entrée, dans le sens défavorable.
        adverse = 1.0 if sig.direction == "long" else -1.0
        entry = sig.entry * (1.0 + adverse * bps)

        future = df.iloc[i + 1:]
        outcome, raw_exit, bars, mfe, mae = simulate_exit(
            future, sig.direction, entry, sig.stop, sig.target, asset.klass)
        if outcome in {"invalide", "tronque"}:
            continue

        exit_price = raw_exit * (1.0 - adverse * bps)
        risk = abs(entry - sig.stop)
        sign = 1.0 if sig.direction == "long" else -1.0
        r_mult = sign * (exit_price - entry) / risk if risk > 0 else 0.0

        trades.append(BacktestTrade(
            symbol=asset.symbol, klass=asset.klass, direction=sig.direction,
            score=sig.score, win_prob=sig.win_prob, expected_r=sig.expected_r,
            rr=sig.rr, regime=sig.regime["name"], entry=round(entry, 8),
            stop=sig.stop, target=sig.target, exit=round(exit_price, 8),
            outcome=outcome, r_multiple=round(r_mult, 3), bars_held=bars,
            mfe=mfe, mae=mae,
            stop_atr=round(risk / sig.atr, 2) if sig.atr > 0 else 0.0,
            stop_basis=sig.stop_basis, target_basis=sig.target_basis,
            stop_strength=getattr(sig, "stop_strength", 0.0),
            obstacle=getattr(sig, "obstacle", 0.0),
            index=i,
        ).to_dict())

    log.info("%s : %d trade(s) simulé(s) sur %d bougies", asset.symbol, len(trades), n)
    return trades


# --------------------------------------------------------------------------
# Analyse des résultats
# --------------------------------------------------------------------------
SCORE_BUCKETS = [(58, 65), (65, 72), (72, 80), (80, 100)]


def calibration(trades: list[dict]) -> dict:
    """Confronte les probabilités prédites aux fréquences observées.

    C'est le test décisif. Si le modèle prédit 40 % de réussite et qu'on en
    observe 40 %, il est calibré. S'il prédit 40 % et qu'on en observe 25 %,
    il est optimiste — et toute l'espérance affichée est fausse.
    """
    if not trades:
        return {"trades": 0, "note": "aucun trade simulé"}

    df = pd.DataFrame(trades)
    df["gagne"] = (df["outcome"] == "cible").astype(int)

    out: dict = {
        "trades": len(df),
        "win_rate_global": round(float(df["gagne"].mean()) * 100, 1),
        "prob_predite_moyenne": round(float(df["win_prob"].mean()) * 100, 1),
        "esperance_predite": round(float(df["expected_r"].mean()), 3),
        "esperance_realisee": round(float(df["r_multiple"].mean()), 3),
        "r_median": round(float(df["r_multiple"].median()), 3),
        "ecart_type_r": round(float(df["r_multiple"].std(ddof=1)), 3) if len(df) > 1 else 0.0,
    }

    # Significativité : une espérance sans intervalle de confiance invite à
    # sur-interpréter. Avec un écart-type de l'ordre de 1.4 R, il faut
    # plusieurs centaines de trades pour distinguer +0.10 R de zéro.
    n = len(df)
    ecart_type = float(df["r_multiple"].std(ddof=1)) if n > 1 else 0.0
    if n > 2 and ecart_type > 0:
        erreur_std = ecart_type / np.sqrt(n)
        moyenne = float(df["r_multiple"].mean())
        t = moyenne / erreur_std
        out["erreur_standard"] = round(erreur_std, 3)
        out["t_statistique"] = round(t, 2)
        out["ic95"] = [round(moyenne - 1.96 * erreur_std, 3),
                       round(moyenne + 1.96 * erreur_std, 3)]
        # |t| > 2 correspond approximativement au seuil de 5 %.
        out["significatif"] = bool(abs(t) > 2.0)
        out["verdict"] = (
            f"espérance {'positive' if moyenne > 0 else 'négative'} et "
            f"statistiquement significative (t={t:.2f})"
            if abs(t) > 2.0 else
            f"espérance indiscernable de zéro (t={t:.2f}, il faudrait environ "
            f"{int((2.0 * ecart_type / max(abs(moyenne), 1e-6)) ** 2)} trades "
            f"pour trancher)")
        # Nombre de trades nécessaire pour détecter l'effet observé.
        if abs(moyenne) > 1e-6:
            out["trades_necessaires"] = int((2.0 * ecart_type / abs(moyenne)) ** 2)

    # Par tranche de score : le score discrimine-t-il vraiment ?
    tranches = []
    for lo, hi in SCORE_BUCKETS:
        sub = df[(df["score"] >= lo) & (df["score"] < hi)]
        if len(sub) < 5:
            continue
        tranches.append({
            "tranche": f"{lo}-{hi}",
            "trades": len(sub),
            "win_rate": round(float(sub["gagne"].mean()) * 100, 1),
            "prob_predite": round(float(sub["win_prob"].mean()) * 100, 1),
            "esperance_realisee": round(float(sub["r_multiple"].mean()), 3),
            "esperance_predite": round(float(sub["expected_r"].mean()), 3),
        })
    out["par_tranche_de_score"] = tranches

    # Le score est-il monotone ? C'est la propriété qui justifie un seuil.
    if len(tranches) >= 2:
        realises = [t["esperance_realisee"] for t in tranches]
        scores = [(SCORE_BUCKETS[i][0] + SCORE_BUCKETS[i][1]) / 2
                  for i, t in enumerate(tranches)]
        if len(set(realises)) > 1:
            corr = float(np.corrcoef(scores[:len(realises)], realises)[0, 1])
            out["correlation_score_esperance"] = round(corr, 3)

    for cle, colonne in (("par_regime", "regime"), ("par_classe", "klass"),
                         ("par_issue", "outcome")):
        groupe = df.groupby(colonne).agg(
            trades=("r_multiple", "size"),
            esperance=("r_multiple", "mean"),
            win_rate=("gagne", "mean"))
        groupe["esperance"] = groupe["esperance"].round(3)
        groupe["win_rate"] = (groupe["win_rate"] * 100).round(1)
        out[cle] = groupe.to_dict("index")

    # Facteur de profit et drawdown sur la séquence chronologique.
    ordered = df.sort_values("index")["r_multiple"]
    gains = float(ordered[ordered > 0].sum())
    pertes = float(abs(ordered[ordered <= 0].sum()))
    out["facteur_de_profit"] = round(gains / pertes, 3) if pertes > 0 else None
    equity = ordered.cumsum()
    if len(equity) > 1:
        out["drawdown_max_R"] = round(float((equity.cummax() - equity).max()), 2)

    return out


def structure_effect(trades: list[dict]) -> dict:
    """Un stop adossé à un niveau structurel est-il moins souvent touché ?

    C'est l'hypothèse qui justifie `STRUCTURE_EDGE`. Elle est plausible — un
    support respecté devrait dévier le prix — mais l'hypothèse inverse l'est
    tout autant : les stops massés derrière un niveau visible constituent une
    poche de liquidité que le marché va précisément chercher.

    Seule la mesure tranche, et c'est ce que fait cette fonction : elle compare
    le devenir des stops adossés à une congestion à celui des stops posés en
    pure volatilité, qui ne s'appuient sur rien.
    """
    if len(trades) < 40:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(trades)
    if "stop_basis" not in df.columns:
        return {"note": "champ stop_basis absent — relancer le backtest"}
    df["gagne"] = (df["outcome"] == "cible").astype(int)
    # « volatilité pure » = aucun niveau derrière le stop.
    df["adosse"] = ~df["stop_basis"].str.startswith("volatilité")

    out: dict = {}
    for adosse, libelle in ((True, "adosse_a_la_structure"), (False, "volatilite_pure")):
        sub = df[df["adosse"] == adosse]
        if len(sub) < 10:
            continue
        out[libelle] = {
            "trades": len(sub),
            "win_rate": round(float(sub["gagne"].mean()) * 100, 1),
            "esperance": round(float(sub["r_multiple"].mean()), 3),
            "rr_moyen": round(float(sub["rr"].mean()), 2),
            # Seuil de rentabilité propre à ce sous-ensemble : sans lui, on
            # comparerait des groupes aux R/R différents.
            "seuil_rentabilite": round(100.0 / (1.0 + float(sub["rr"].mean())), 1),
        }
    for v in out.values():
        if isinstance(v, dict):
            v["ecart_au_seuil"] = round(v["win_rate"] - v["seuil_rentabilite"], 1)
    return out


def penalty_effects(trades: list[dict]) -> dict:
    """Confronte les deux pénalités supposées aux faits.

    `NOISE_PENALTY` et `OBSTACLE_PENALTY` n'ont jamais été mesurées, alors
    qu'elles retranchent jusqu'à 0.415 de probabilité — plus de trois fois
    l'avantage maximal, lui mesuré, de 0.12. Un terme supposé peut donc
    opposer son veto à un terme mesuré, ce qui est exactement l'erreur que ce
    projet s'attache à corriger ailleurs.

    La mesure exige un échantillon **non filtré** : lancer le backtest avec
    `JIMBOT_MIN_EXPECTED_R=-999` pour que tout signal au-dessus du seuil de
    score soit pris, quelle que soit son espérance.

    Pour chaque tranche, on compare le taux de réussite observé au seuil de
    rentabilité propre à la tranche — sans quoi on comparerait des groupes de
    R/R différents.
    """
    if len(trades) < 60:
        return {"note": "échantillon insuffisant"}

    df = pd.DataFrame(trades)
    df["gagne"] = (df["outcome"] == "cible").astype(int)

    def tranches(colonne: str, decoupe) -> list[dict]:
        if colonne not in df.columns or df[colonne].nunique() < 3:
            return []
        try:
            df["_t"] = decoupe(df[colonne])
        except ValueError:
            return []
        out = []
        for tranche, sub in df.groupby("_t", observed=True):
            if len(sub) < 15:
                continue
            rr = float(sub["rr"].mean())
            seuil = 100.0 / (1.0 + rr)
            win = float(sub["gagne"].mean()) * 100
            out.append({
                "tranche": str(tranche),
                "trades": len(sub),
                "win_rate": round(win, 1),
                "seuil_rentabilite": round(seuil, 1),
                # L'écart au seuil est la seule grandeur comparable entre
                # tranches : il isole l'effet du critère mesuré.
                "ecart_au_seuil": round(win - seuil, 1),
                "esperance": round(float(sub["r_multiple"].mean()), 3),
                "prob_predite": round(float(sub["win_prob"].mean()) * 100, 1),
            })
        return out

    return {
        "par_distance_de_stop_atr": tranches(
            "stop_atr", lambda c: pd.cut(c, [0, 1.2, 1.6, 2.2, 3.0, 99],
                                         labels=["<1.2", "1.2-1.6", "1.6-2.2",
                                                 "2.2-3.0", ">3.0"])),
        "par_obstacle": tranches(
            "obstacle", lambda c: pd.cut(c, [-0.01, 0.01, 0.5, 1.0, 99],
                                         labels=["aucun", "faible", "moyen", "fort"])),
        "par_solidite_du_stop": tranches(
            "stop_strength", lambda c: pd.cut(c, [-0.01, 0.01, 0.5, 0.9, 1.01],
                                              labels=["aucune", "faible",
                                                      "moyenne", "forte"])),
    }


def edge_by_distance(trades: list[dict], bins: int = 4) -> list[dict]:
    """Mesure la décroissance de l'avantage avec la distance de l'objectif.

    `EDGE_HORIZON_ATR` est posé a priori dans le module de niveaux. Cette
    fonction permet de le confronter aux données : si l'avantage ne décroît
    pas comme supposé, la constante est à revoir.
    """
    if len(trades) < 20:
        return []
    df = pd.DataFrame(trades)
    df["gagne"] = (df["outcome"] == "cible").astype(int)
    df["distance_atr"] = df["stop_atr"] * df["rr"]

    try:
        df["tranche"] = pd.qcut(df["distance_atr"], bins, duplicates="drop")
    except ValueError:
        return []

    out = []
    for tranche, sub in df.groupby("tranche", observed=True):
        if len(sub) < 5:
            continue
        out.append({
            "distance_atr": f"{tranche.left:.1f}-{tranche.right:.1f}",
            "trades": len(sub),
            "win_rate": round(float(sub["gagne"].mean()) * 100, 1),
            "prob_predite": round(float(sub["win_prob"].mean()) * 100, 1),
            # L'écart entre prédiction et réalité, en points de probabilité :
            # positif si le modèle est trop optimiste.
            "biais": round(float(sub["win_prob"].mean() - sub["gagne"].mean()) * 100, 1),
            "esperance_realisee": round(float(sub["r_multiple"].mean()), 3),
        })
    return out
