"""Validation hors échantillon des pondérations.

Le péché originel de tout travail quantitatif : dériver des paramètres d'un
échantillon, puis mesurer leur performance sur ce même échantillon. Le
résultat est flatteur par construction, et ne dit rien de ce qui se passera
demain.

Ce module sépare strictement les deux opérations. Les pondérations sont
ajustées sur une période, puis évaluées sur une période **postérieure**,
jamais vue. Trois protocoles complémentaires :

1. **Découpage calendaire strict** — tout ce qui précède une date sert à
   ajuster, tout ce qui suit sert à mesurer. C'est le test le plus honnête :
   aucune information future ne peut atteindre l'ajustement, y compris à
   travers un autre actif.

2. **Découpage par actif** — chaque actif est coupé sur sa propre chronologie.
   La composition de l'univers reste identique entre les deux périodes, ce
   qui évite de comparer un échantillon d'apprentissage majoritairement
   composé d'indices à un échantillon de test majoritairement crypto. En
   contrepartie, les périodes se chevauchent d'un actif à l'autre.

3. **Walk-forward glissant** — l'ajustement est refait à chaque bloc sur tout
   le passé disponible, et évalué sur le bloc suivant. C'est le protocole qui
   ressemble le plus à l'exploitation réelle, et le seul qui montre si les
   pondérations sont stables dans le temps.

Une pondération qui ne survit pas à ces trois tests n'est pas une découverte,
c'est un artefact.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .probe import FACTORS, _spearman

log = logging.getLogger("jimbot.validation")

# Seuil de significativité pour qu'un facteur entre dans les pondérations.
T_MIN = 2.0

# Horizon de référence. Choisi parce que le coefficient d'information y est le
# plus élevé parmi ceux mesurés, tout en restant compatible avec la durée de
# détention observée.
HORIZON = 48


def _ic(sub: pd.DataFrame, nom: str, col: str) -> tuple[float, float]:
    """Coefficient d'information et statistique t d'un facteur."""
    paire = sub[[nom, col]].dropna()
    if len(paire) < 50 or paire[nom].nunique() < 5:
        return float("nan"), 0.0
    ic = _spearman(paire[nom], paire[col])
    if not np.isfinite(ic):
        return float("nan"), 0.0
    t = ic * np.sqrt((len(paire) - 2) / max(1e-9, 1 - ic ** 2))
    return ic, float(t)


def fit_weights(train: pd.DataFrame, horizon: int = HORIZON) -> dict[str, float]:
    """Dérive les pondérations des coefficients mesurés sur l'échantillon.

    Les poids sont proportionnels aux coefficients, signes compris : un
    facteur qui prédit à l'envers reçoit un poids négatif. Les facteurs non
    significatifs reçoivent zéro — les inclure reviendrait à ajuster sur du
    bruit, ce qui est précisément ce que la validation cherche à détecter.
    """
    col = f"fwd_{horizon}"
    bruts: dict[str, float] = {}
    for nom in FACTORS:
        if nom not in train.columns:
            continue
        ic, t = _ic(train, nom, col)
        bruts[nom] = ic if (np.isfinite(ic) and abs(t) >= T_MIN) else 0.0

    total = sum(abs(v) for v in bruts.values())
    if total <= 0:
        return {nom: 0.0 for nom in bruts}
    return {nom: round(v / total, 4) for nom, v in bruts.items()}


def composite(df: pd.DataFrame, poids: dict[str, float]) -> pd.Series:
    """Score composite : somme pondérée des facteurs."""
    presents = [nom for nom in poids if nom in df.columns and poids[nom] != 0]
    if not presents:
        return pd.Series(np.zeros(len(df)), index=df.index)
    return sum(df[nom] * poids[nom] for nom in presents)


def evaluate(test: pd.DataFrame, poids: dict[str, float],
             horizon: int = HORIZON) -> dict:
    """Mesure le pouvoir prédictif d'un jeu de poids sur des données non vues."""
    col = f"fwd_{horizon}"
    score = composite(test, poids)
    paire = pd.DataFrame({"s": score, "f": test[col]}).dropna()
    if len(paire) < 50 or paire["s"].nunique() < 5:
        return {"observations": len(paire), "ic": None,
                "note": "échantillon insuffisant"}

    ic = _spearman(paire["s"], paire["f"])
    t = ic * np.sqrt((len(paire) - 2) / max(1e-9, 1 - ic ** 2))
    return {
        "observations": len(paire),
        "ic": round(float(ic), 4),
        "t": round(float(t), 2),
        "significatif": bool(abs(t) > T_MIN),
    }


def _prepare(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True, format="ISO8601")
    return df.sort_values("t").reset_index(drop=True)


# --------------------------------------------------------------------------
# Protocoles
# --------------------------------------------------------------------------
def holdout_calendaire(rows: list[dict], train_frac: float = 0.6,
                       horizon: int = HORIZON) -> dict:
    """Ajustement sur tout ce qui précède une date, mesure sur ce qui suit."""
    df = _prepare(rows)
    coupure = df["t"].quantile(train_frac)
    train, test = df[df["t"] <= coupure], df[df["t"] > coupure]
    if len(train) < 500 or len(test) < 500:
        return {"note": "échantillon insuffisant pour un découpage calendaire"}

    poids = fit_weights(train, horizon)
    return {
        "coupure": coupure.isoformat(),
        "poids_ajustes": poids,
        "train": {"observations": len(train),
                  "actifs": sorted(train["symbol"].unique().tolist()),
                  **evaluate(train, poids, horizon)},
        "test": {"observations": len(test),
                 "actifs": sorted(test["symbol"].unique().tolist()),
                 **evaluate(test, poids, horizon)},
    }


def holdout_par_actif(rows: list[dict], train_frac: float = 0.6,
                      horizon: int = HORIZON) -> dict:
    """Chaque actif est coupé sur sa propre chronologie.

    Préserve la composition de l'univers entre les deux périodes, au prix
    d'un chevauchement calendaire entre actifs.
    """
    df = _prepare(rows)
    trains, tests = [], []
    for _, groupe in df.groupby("symbol"):
        g = groupe.sort_values("t")
        coupe = int(len(g) * train_frac)
        trains.append(g.iloc[:coupe])
        tests.append(g.iloc[coupe:])
    train, test = pd.concat(trains), pd.concat(tests)

    poids = fit_weights(train, horizon)
    return {
        "poids_ajustes": poids,
        "train": {"observations": len(train), **evaluate(train, poids, horizon)},
        "test": {"observations": len(test), **evaluate(test, poids, horizon)},
    }


def walk_forward(rows: list[dict], n_blocs: int = 5,
                 horizon: int = HORIZON) -> dict:
    """Ajustement sur tout le passé, mesure sur le bloc suivant, et on avance.

    Le protocole le plus proche de l'exploitation réelle : à chaque étape, on
    ne dispose que de ce qui était connu à ce moment-là.
    """
    df = _prepare(rows)
    bornes = [df["t"].quantile(q) for q in np.linspace(0, 1, n_blocs + 1)]

    plis: list[dict] = []
    for i in range(1, n_blocs):
        fin_train = bornes[i]
        fin_test = bornes[i + 1]
        train = df[df["t"] <= fin_train]
        test = df[(df["t"] > fin_train) & (df["t"] <= fin_test)]
        if len(train) < 500 or len(test) < 200:
            continue

        poids = fit_weights(train, horizon)
        plis.append({
            "bloc": i,
            "periode_test": [fin_train.isoformat()[:10], fin_test.isoformat()[:10]],
            "train_obs": len(train),
            "poids": poids,
            "in_sample": evaluate(train, poids, horizon),
            "hors_echantillon": evaluate(test, poids, horizon),
        })

    if not plis:
        return {"note": "échantillon insuffisant"}

    ics = [p["hors_echantillon"].get("ic") for p in plis
           if p["hors_echantillon"].get("ic") is not None]
    resume = {}
    if ics:
        resume = {
            "ic_moyen_hors_echantillon": round(float(np.mean(ics)), 4),
            "ic_median": round(float(np.median(ics)), 4),
            "blocs_positifs": f"{sum(1 for x in ics if x > 0)}/{len(ics)}",
            "ecart_type": round(float(np.std(ics, ddof=1)), 4) if len(ics) > 1 else 0.0,
        }
    return {"plis": plis, "resume": resume,
            "stabilite_des_poids": weight_stability([p["poids"] for p in plis])}


def weight_stability(jeux: list[dict[str, float]]) -> dict:
    """Les pondérations ajustées se ressemblent-elles d'un bloc à l'autre ?

    Des poids qui changent de signe d'un bloc au suivant révèlent un
    ajustement sur du bruit, même si chaque bloc paraît significatif pris
    isolément.
    """
    if len(jeux) < 2:
        return {"note": "trop peu de blocs"}

    noms = sorted({n for j in jeux for n in j})
    matrice = pd.DataFrame([[j.get(n, 0.0) for n in noms] for j in jeux], columns=noms)

    out: dict = {"par_facteur": {}}
    for nom in noms:
        serie = matrice[nom]
        signes = {np.sign(v) for v in serie if v != 0}
        out["par_facteur"][nom] = {
            "moyenne": round(float(serie.mean()), 4),
            "ecart_type": round(float(serie.std(ddof=0)), 4),
            # Un facteur dont le signe change n'est pas exploitable.
            "signe_stable": len(signes) <= 1,
            "valeurs": [round(float(v), 3) for v in serie],
        }

    # Corrélation moyenne entre jeux de poids consécutifs.
    correlations = []
    for i in range(len(matrice) - 1):
        a, b = matrice.iloc[i], matrice.iloc[i + 1]
        if a.std() > 0 and b.std() > 0:
            correlations.append(float(a.corr(b)))
    if correlations:
        out["correlation_entre_blocs"] = round(float(np.mean(correlations)), 3)
    return out


def baseline_naive(rows: list[dict], horizon: int = HORIZON) -> dict:
    """Référence : retour à la moyenne seul, sans aucun ajustement.

    Si les pondérations ajustées ne battent pas ce point de comparaison, tout
    l'appareil de mesure n'aura servi qu'à retrouver, en plus compliqué, le
    seul facteur dont le signe était correct dès le départ.
    """
    df = _prepare(rows)
    return evaluate(df, {"mean_reversion": 1.0}, horizon)
