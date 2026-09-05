"""Moteur de signal : scoring multi-facteurs pondéré par le régime de marché.

Principe directeur : aucun indicateur n'est fiable partout. Un croisement de
moyennes mobiles est excellent en tendance et catastrophique en range ; un
RSI en survente est un signal d'achat en range et un signal de continuation
baissière en tendance. Le moteur calcule donc d'abord le régime, puis
applique le jeu de pondérations correspondant.

Chaque facteur est normalisé dans [-1, +1] et conserve la trace de son calcul,
pour que le rapport puisse expliquer *pourquoi* un signal a été émis. Aucun
chiffre du rapport n'est produit ailleurs qu'ici.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

from . import indicators as I
from . import levels as L
from . import stats as S
from .config import Asset, RISK, SETTINGS, _env


# Constante de calibrage du score.
#
# La somme pondérée des facteurs est bornée dans [-1, +1] par construction,
# mais sa distribution réelle dépend entièrement des pondérations. Le
# calibrage doit donc être mesuré, pas supposé, et refait chaque fois que les
# poids changent.
#
# Valeur dérivée de 17 070 observations (`engine/probe_run.py`) : le 90e
# percentile de |somme pondérée| vaut 0.628, et SCORE_SCALE = 0.947 fait
# correspondre ce percentile au seuil de 58. Un signal est donc émis sur les
# 10 % de lectures les plus nettes, ce qui est le sens voulu d'un seuil.
#
# La valeur précédente, 0.36, avait été calibrée sur les anciennes
# pondérations. Conservée après leur inversion, elle plaçait 73,5 % des
# observations au-dessus du seuil — celui-ci ne sélectionnait plus rien.
SCORE_SCALE = 0.947

# Espérance minimale, en multiples de risque, pour qu'un signal soit retenu.
# Un score élevé ne suffit pas : si la structure ne laisse aucun objectif
# atteignable avant une résistance solide, le trade est mauvais quelle que
# soit la conviction. C'est le principal apport de l'optimisation des niveaux.
# Espérance minimale exigée d'un plan, en multiples de risque.
#
# Ce seuil ne sert plus qu'à écarter les plans franchement défavorables, et
# c'est délibéré. Mesuré sur 500 trades non filtrés, l'espérance estimée ne
# classe pas correctement les trades : la tranche la mieux notée (+0.053 R
# estimé) réalise -0.085 R, tandis que la tranche neutre (+0.001 R estimé)
# réalise +0.176 R. La relation n'est pas seulement bruitée, elle n'est pas
# monotone.
#
# Autrement dit, le score prédit bien la direction — coefficient
# d'information de +0.063, validé hors échantillon — mais l'appareil qui
# traduit ce signal en plan de trade ne parvient pas à en tirer une
# sélection utile. Le défaut est dans la traduction, pas dans le signal.
#
# Régler finement un seuil sur une grandeur qui ne discrimine pas
# reviendrait à ajuster le tirage au sort. Il est donc ramené à zéro : on
# refuse ce qui est estimé perdant, sans prétendre hiérarchiser le reste.
# La suite consiste à comparer cet optimiseur à un plan fixe et robuste —
# stop à 2 ATR, objectif à 2 R — pour établir s'il apporte quoi que ce soit.
MIN_EXPECTED_R = float(_env("JIMBOT_MIN_EXPECTED_R", "0.0"))

# --------------------------------------------------------------------------
# Pondérations par régime : la même donnée ne vaut pas la même chose partout.
# Chaque colonne somme à 1.0.
# --------------------------------------------------------------------------
# Pondérations dérivées de la mesure, et non plus supposées.
#
# La version précédente distinguait quatre jeux de poids selon le régime,
# posés a priori et jamais vérifiés. La sonde de pouvoir prédictif
# (`engine/probe_run.py`, 17 070 observations sur 15 actifs) les a démentis
# sur deux points :
#
# 1. **Les facteurs de suivi de tendance prédisent à l'envers.** Coefficients
#    d'information à 48 bougies : trend -0.065 (t=-7.0), structure -0.064,
#    breakout -0.037 — tous significatifs, et le signe se confirme sur les
#    quatre horizons mesurés. Une lecture haussière du prix est suivie, en
#    moyenne, d'un rendement négatif. Le seul facteur dont le signe était
#    correct est le retour à la moyenne (+0.033).
#
#    C'est l'explication du défaut central relevé par le backtest : le score
#    ne discriminait pas parce que son facteur dominant était inversé.
#
# 2. **Le régime ne justifie pas des jeux de poids distincts.** Seuls les
#    régimes « chaotique » et « range » présentent des coefficients
#    significatifs, et ils portent les mêmes signes que la mesure globale.
#    Quatre jeux de poids revenaient donc à ajuster des paramètres sur du
#    bruit. Un jeu unique est retenu : moins de paramètres, moins de
#    surajustement, et il reste soutenu par les données.
#
# Le régime continue de moduler la *confiance* via `confidence_mult` — un
# marché sans structure reste moins exploitable — mais plus la hiérarchie
# des facteurs.
#
# Les poids sont proportionnels aux coefficients mesurés, les facteurs non
# significatifs (volume, momentum) étant mis à zéro plutôt que conservés au
# prétexte qu'ils existent. Le sentiment garde un poids modeste : il n'est pas
# mesurable sur l'historique, faute de flux de presse reconstituable.
MEASURED_WEIGHTS: dict[str, float] = {
    "trend": -0.295,
    "structure": -0.290,
    "breakout": -0.165,
    "mean_reversion": +0.150,
    "volume": 0.0,
    "momentum": 0.0,
    "sentiment": +0.100,
}

WEIGHTS: dict[str, dict[str, float]] = {
    regime: dict(MEASURED_WEIGHTS)
    for regime in ("tendance_haussière", "tendance_baissière", "range", "chaotique")
}

@dataclass
class Factor:
    """Un facteur noté, avec sa justification lisible."""

    name: str
    value: float          # normalisé dans [-1, +1]
    weight: float
    detail: str           # explication en clair, reprise telle quelle dans le rapport

    @property
    def contribution(self) -> float:
        return self.value * self.weight

    def to_dict(self) -> dict:
        d = asdict(self)
        d["contribution"] = round(self.contribution, 4)
        return d


@dataclass
class Signal:
    """Résultat complet de l'analyse d'un actif."""

    symbol: str
    label: str
    klass: str
    direction: str        # "long" | "short" | "neutre"
    score: float          # 0-100, force du signal (signe porté par direction)
    raw_score: float      # -100 à +100 avant application du régime
    price: float
    regime: dict
    factors: list[dict]
    entry: float
    stop: float
    target: float
    rr: float
    atr: float
    atr_pct: float
    timeframe: str
    htf_alignment: float  # -1 à +1 : accord avec l'unité de temps supérieure
    news_score: float
    news_count: int
    generated_at: str
    # Orientation de la lecture, indépendamment du seuil : un actif sous le
    # seuil garde un biais exploitable pour une liste de surveillance.
    bias: str = "neutre"       # "long" | "short" | "neutre"
    actionable: bool = False   # le signal franchit-il le seuil et l'espérance minimale
    win_prob: float = 0.0      # probabilité estimée d'atteindre l'objectif avant le stop
    expected_r: float = 0.0    # espérance du trade, en multiples de risque
    stop_basis: str = ""       # niveau structurel justifiant le stop
    target_basis: str = ""     # niveau structurel justifiant l'objectif
    # Entrées des pénalités : supposées, contrairement à l'avantage qui est
    # mesuré. Conservées pour pouvoir les confronter aux faits.
    stop_strength: float = 0.0
    obstacle: float = 0.0
    plan_alternatives: list = field(default_factory=list)
    # Ce que l'actif fait en ce moment. Mesuré, jamais prédit, et sans aucun
    # effet sur le score : voir `mouvement()`.
    mouvement: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Facteurs
# --------------------------------------------------------------------------
def _squash(x: float, scale: float) -> float:
    """Compresse une grandeur non bornée dans [-1, 1] via tanh.

    tanh évite les seuils arbitraires : une valeur extrême sature au lieu de
    dominer la somme pondérée.
    """
    if not np.isfinite(x):
        return 0.0
    return float(np.tanh(x / scale))


def factor_trend(df: pd.DataFrame) -> Factor:
    """Alignement des moyennes mobiles et position du prix.

    Trois sous-critères : ordre des EMA (structure), position du prix par
    rapport à l'EMA 50 en unités d'ATR (extension), pente de la régression.
    """
    close = df["close"]
    e20, e50, e200 = I.ema(close, 20), I.ema(close, 50), I.ema(close, 200)
    price = S._last(close)
    v20, v50, v200 = S._last(e20), S._last(e50), S._last(e200)
    atr_v = S._last(I.atr(df["high"], df["low"], close))

    parts, detail = [], []
    if np.isfinite(v20) and np.isfinite(v50):
        stack = 1.0 if v20 > v50 else -1.0
        if np.isfinite(v200):
            # Alignement complet des trois EMA = structure de tendance nette.
            full = (v20 > v50 > v200) or (v20 < v50 < v200)
            stack *= 1.0 if full else 0.55
            detail.append("EMA 20/50/200 alignées" if full else "EMA partiellement alignées")
        parts.append(stack)

    if np.isfinite(v50) and atr_v > 0:
        # Distance à l'EMA 50 en ATR : mesure l'extension, saturée à ±2 ATR.
        ext = (price - v50) / atr_v
        parts.append(_squash(ext, 2.0))
        detail.append(f"prix à {ext:+.1f} ATR de l'EMA 50")

    slope_s, r2_s = I.slope_r2(close, 40)
    slope, r2 = S._last(slope_s, 0.0), S._last(r2_s, 0.0)
    if np.isfinite(slope):
        # La pente n'est retenue qu'à hauteur de la qualité de l'ajustement.
        parts.append(_squash(slope * 100, 0.5) * float(np.clip(r2, 0, 1)))
        detail.append(f"pente {slope * 100:+.2f} %/bougie (R²={r2:.2f})")

    val = float(np.mean(parts)) if parts else 0.0
    return Factor("trend", round(val, 4), 0.0, " · ".join(detail) or "données insuffisantes")


def factor_momentum(df: pd.DataFrame) -> Factor:
    """Accélération : histogramme MACD, RSI, taux de variation."""
    close = df["close"]
    _, _, hist = I.macd(close)
    hist_z = S.zscore(hist, 60)
    hz = S._last(hist_z, 0.0)
    rsi_v = S._last(I.rsi(close), 50.0)
    roc_v = S._last(I.roc(close, 10), 0.0)

    parts = [
        _squash(hz, 1.5),
        # RSI recentré sur 50 : 70 -> +0.66, 30 -> -0.66
        _squash((rsi_v - 50.0) / 10.0, 2.0),
        _squash(roc_v, 6.0),
    ]
    val = float(np.mean(parts))
    detail = f"MACD hist z={hz:+.2f} · RSI {rsi_v:.0f} · ROC(10) {roc_v:+.1f} %"
    return Factor("momentum", round(val, 4), 0.0, detail)


def factor_mean_reversion(df: pd.DataFrame) -> Factor:
    """Sur-extension : %B des Bollinger, écart au VWAP, RSI extrême.

    Signe inversé : un prix collé à la bande haute produit une valeur
    négative (signal de vente), car ce facteur mesure l'attraction vers la
    moyenne, pas la continuation.
    """
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    _, _, _, pct_b, width = I.bollinger(close, 20, 2.0)
    b = S._last(pct_b, 0.5)
    rsi_v = S._last(I.rsi(close), 50.0)
    vw = S._last(I.vwap(high, low, close, vol, 20))
    price = S._last(close)
    atr_v = S._last(I.atr(high, low, close))

    parts = [-_squash((b - 0.5) * 2.0, 0.8), -_squash((rsi_v - 50.0) / 10.0, 2.5)]
    detail = [f"%B={b:.2f}", f"RSI {rsi_v:.0f}"]
    if np.isfinite(vw) and atr_v > 0:
        gap = (price - vw) / atr_v
        parts.append(-_squash(gap, 1.5))
        detail.append(f"écart VWAP {gap:+.1f} ATR")

    val = float(np.mean(parts))
    return Factor("mean_reversion", round(val, 4), 0.0, " · ".join(detail))


def factor_volume(df: pd.DataFrame) -> Factor:
    """Confirmation par le volume : le mouvement est-il financé ?

    Un volume anormal n'a pas de direction propre — il amplifie le sens du
    mouvement de prix qui l'accompagne.
    """
    close, volume = df["close"], df["volume"]
    if volume.tail(50).sum() <= 0:
        # Le forex Yahoo ne fournit pas de volume : facteur neutralisé.
        return Factor("volume", 0.0, 0.0, "volume non disponible sur cette source")

    vz = S._last(I.volume_zscore(volume, 30), 0.0)
    ret5 = S._last(I.roc(close, 5), 0.0)
    obv_slope, obv_r2 = I.slope_r2(I.obv(close, volume).abs().clip(lower=1.0), 30)
    ob = S._last(obv_slope, 0.0)

    direction = math.copysign(1.0, ret5) if ret5 != 0 else 0.0
    amplification = _squash(max(vz, 0.0), 1.5) * direction
    parts = [amplification, _squash(ob * 100, 2.0) * float(np.clip(S._last(obv_r2, 0.0), 0, 1))]

    val = float(np.mean(parts))
    detail = f"volume z={vz:+.2f} · variation 5 bougies {ret5:+.1f} % · pente OBV {ob * 100:+.2f}"
    return Factor("volume", round(val, 4), 0.0, detail)


def factor_breakout(df: pd.DataFrame) -> Factor:
    """Cassure de canal de Donchian et sortie de compression Bollinger."""
    close, high, low = df["close"], df["high"], df["low"]
    dlow, dhigh = I.donchian(high, low, 20)
    price = S._last(close)
    dl, dh = S._last(dlow), S._last(dhigh)
    atr_v = S._last(I.atr(high, low, close))

    parts, detail = [], []
    if np.isfinite(dh) and np.isfinite(dl) and atr_v > 0:
        if price > dh:
            parts.append(_squash((price - dh) / atr_v, 0.8))
            detail.append(f"cassure haussière du canal 20 (+{(price - dh) / atr_v:.1f} ATR)")
        elif price < dl:
            parts.append(-_squash((dl - price) / atr_v, 0.8))
            detail.append(f"cassure baissière du canal 20 (-{(dl - price) / atr_v:.1f} ATR)")
        else:
            span = max(dh - dl, 1e-12)
            pos = (price - dl) / span  # 0 = bas du canal, 1 = haut
            parts.append((pos - 0.5) * 0.6)
            detail.append(f"dans le canal, position {pos * 100:.0f} %")

    _, _, _, _, width = I.bollinger(close, 20, 2.0)
    w_hist = width.dropna()
    if len(w_hist) > 30:
        w_now = float(w_hist.iloc[-1])
        pct = float((w_hist < w_now).mean())
        if pct < 0.15:
            # Compression extrême : mouvement imminent, direction encore inconnue.
            # On n'ajoute donc pas de biais directionnel, on le signale.
            detail.append(f"compression Bollinger (percentile {pct * 100:.0f} %)")

    val = float(np.mean(parts)) if parts else 0.0
    return Factor("breakout", round(val, 4), 0.0, " · ".join(detail) or "pas de cassure")


def factor_structure(df: pd.DataFrame) -> Factor:
    """Structure de marché : Supertrend, nuage Ichimoku et divergences.

    Ce facteur regroupe les signaux qui portent sur la *forme* du marché
    plutôt que sur sa vitesse. La divergence y occupe une place à part :
    c'est l'un des rares indicateurs réellement avancés, puisqu'il détecte
    l'essoufflement d'une impulsion avant que le prix ne se retourne.
    """
    close, high, low = df["close"], df["high"], df["low"]
    parts, detail = [], []

    _, direction = I.supertrend(high, low, close)
    st_dir = S._last(direction, 0.0)
    if np.isfinite(st_dir) and st_dir != 0:
        parts.append(float(np.clip(st_dir, -1, 1)))
        detail.append(f"Supertrend {'haussier' if st_dir > 0 else 'baissier'}")

    _, _, span_a, span_b, _ = I.ichimoku(high, low, close)
    a, b = S._last(span_a), S._last(span_b)
    price = S._last(close)
    atr_v = S._last(I.atr(high, low, close))
    if np.isfinite(a) and np.isfinite(b) and atr_v > 0:
        cloud_top, cloud_bottom = max(a, b), min(a, b)
        if price > cloud_top:
            parts.append(_squash((price - cloud_top) / atr_v, 1.5))
            detail.append("au-dessus du nuage Ichimoku")
        elif price < cloud_bottom:
            parts.append(-_squash((cloud_bottom - price) / atr_v, 1.5))
            detail.append("sous le nuage Ichimoku")
        else:
            parts.append(0.0)
            detail.append("dans le nuage Ichimoku (indécision)")

    div_value, div_label = I.divergence(close, I.rsi(close))
    if div_value != 0.0:
        # La divergence pèse double : elle contredit le mouvement en cours,
        # ce qui est rare et informatif.
        parts.append(div_value)
        parts.append(div_value)
        detail.append(div_label)

    val = float(np.mean(parts)) if parts else 0.0
    return Factor("structure", round(val, 4), 0.0,
                  " · ".join(detail) or "structure indéterminée")


def factor_sentiment(news_score: float, count: int) -> Factor:
    """Sentiment de presse, déjà borné dans [-1, 1] par la couche news."""
    if count == 0:
        return Factor("sentiment", 0.0, 0.0, "aucune actualité rattachée")
    return Factor("sentiment", round(float(news_score), 4), 0.0,
                  f"{count} article(s), sentiment pondéré {news_score:+.2f}")


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------
def htf_bias(df_htf: pd.DataFrame | None) -> tuple[float, str]:
    """Biais de l'unité de temps supérieure, dans [-1, +1].

    Prendre un long alors que l'unité supérieure est baissière est le moyen le
    plus fiable de perdre : ce biais sert de multiplicateur, pas de facteur.
    """
    if df_htf is None or len(df_htf) < 60:
        return 0.0, "unité supérieure indisponible"
    close = df_htf["close"]
    e20, e50 = S._last(I.ema(close, 20)), S._last(I.ema(close, 50))
    price = S._last(close)
    if not (np.isfinite(e20) and np.isfinite(e50)):
        return 0.0, "unité supérieure indisponible"
    bias = 0.6 * math.copysign(1.0, e20 - e50) + 0.4 * math.copysign(1.0, price - e50)
    label = "haussière" if bias > 0 else ("baissière" if bias < 0 else "neutre")
    return round(bias, 3), f"unité supérieure {label} (biais {bias:+.2f})"


def _variation(df: pd.DataFrame, heures: float) -> float | None:
    """Variation en pourcentage sur les `heures` écoulées, ou None.

    Le décompte se fait sur les horodatages et non sur un nombre de bougies :
    l'heure de Yahoo saute les week-ends et les séances fermées, si bien que
    « 24 bougies en arrière » désignerait avant-hier sur un indice et hier sur
    une crypto. Comparer deux actifs supposait de comparer deux durées.

    Renvoie None — et non zéro — quand l'historique ne remonte pas assez loin.
    Un actif ajouté hier n'a pas « fait 0 % sur sept jours » : on ne sait pas,
    et l'écrire zéro le placerait au milieu d'un classement de variations.
    """
    close = df["close"]
    if len(close) < 2 or not isinstance(close.index, pd.DatetimeIndex):
        return None
    fin = close.index[-1]
    cible = fin - pd.Timedelta(hours=heures)
    anterieurs = close.loc[close.index <= cible]
    if anterieurs.empty:
        return None
    depart = float(anterieurs.iloc[-1])
    arrivee = float(close.iloc[-1])
    if not (depart > 0 and np.isfinite(depart) and np.isfinite(arrivee)):
        return None
    return (arrivee / depart - 1.0) * 100.0


def _amplitude_habituelle(df: pd.DataFrame, jours: int = 30) -> float | None:
    """Amplitude médiane d'une fenêtre de 24 h pour cet actif, en pourcentage.

    Sert d'étalon au parcours du jour : « deux fois sa journée ordinaire » est
    comparable d'un actif à l'autre, là où « 3,4 % » ne l'est pas. La médiane
    plutôt que la moyenne, pour que la journée de krach qu'on cherche à
    détecter ne devienne pas elle-même la référence.

    Renvoie None si l'historique ne couvre pas au moins une semaine : sans
    étalon, on préfère ne rien qualifier plutôt que qualifier sur trois jours.
    """
    if len(df) < 24 * 7 or not isinstance(df.index, pd.DatetimeIndex):
        return None
    recent = df.loc[df.index >= df.index[-1] - pd.Timedelta(days=jours)]
    if len(recent) < 24 * 7:
        recent = df
    haut = recent["high"].rolling(24, min_periods=12).max()
    bas = recent["low"].rolling(24, min_periods=12).min()
    etendue = ((haut - bas) / recent["close"] * 100.0).dropna()
    if etendue.empty:
        return None
    ref = float(etendue.median())
    return ref if ref > 0 and np.isfinite(ref) else None


def mouvement(df: pd.DataFrame) -> dict:
    """Ce que l'actif est en train de faire, indépendamment de toute prédiction.

    Le site savait dire ce que le moteur *pense* d'un actif, et ce qu'un signal
    passé *a donné*. Il ne savait pas dire ce que le marché *fait* — or c'est la
    première chose qu'on regarde en ouvrant un écran, et c'est la seule qui ne
    demande aucun modèle : ce sont des mesures, pas des estimations.

    Aucun de ces chiffres n'entre dans le score. Ils décrivent, ils ne prédisent
    pas, et les mélanger reviendrait à rajouter des facteurs non mesurés à un
    moteur dont la sonde a précisément montré que ses facteurs supposés
    prédisaient à l'envers.
    """
    close = df["close"]
    if not isinstance(close.index, pd.DatetimeIndex) or len(close) < 2:
        return {"disponible": False}

    var_1h = _variation(df, 1)
    var_24h = _variation(df, 24)
    var_7j = _variation(df, 24 * 7)

    # Sans variation sur 24 h, il n'y a pas de mouvement à décrire : mieux vaut
    # une case vide qu'une étiquette calculée sur rien.
    if var_24h is None:
        return {"disponible": False}

    # Amplitude réellement parcourue sur 24 h, et non écart entre les deux
    # extrémités. La distinction n'est pas théorique : un actif qui monte de
    # 6 % puis rend tout affiche une variation nette de +0,3 %, et se lisait
    # « calme » alors qu'il venait de parcourir six pour cent. C'est le
    # parcours qui dit l'agitation ; la variation nette dit ce qu'il en reste.
    fenetre = df.loc[df.index >= df.index[-1] - pd.Timedelta(hours=24)]
    haut = float(fenetre["high"].max()) if len(fenetre) else float("nan")
    bas = float(fenetre["low"].min()) if len(fenetre) else float("nan")
    prix = float(close.iloc[-1])

    if np.isfinite(haut) and np.isfinite(bas) and prix > 0 and haut > bas:
        amplitude_pct = (haut - bas) / prix * 100.0
        position = (prix - bas) / (haut - bas)
    else:
        amplitude_pct = abs(var_24h)
        position = 0.5

    # Le parcours rapporté à la journée *habituelle de cet actif*, et non à un
    # ATR. L'ATR est calculé sur des bougies d'une heure : le rapporter à une
    # amplitude de 24 h compare deux durées différentes, et sur les données
    # réelles tous les actifs ressortaient entre 6 et 10 ATR, c'est-à-dire
    # « violents » en permanence — un indicateur qui dit toujours la même
    # chose n'informe pas.
    #
    # La référence est donc mesurée : la médiane des amplitudes glissantes sur
    # 24 h de l'historique disponible. « Deux fois sa journée ordinaire » se
    # compare d'un actif à l'autre sans rien supposer de la distribution.
    amplitude_ref = _amplitude_habituelle(df)
    ampleur = amplitude_pct / amplitude_ref if amplitude_ref else 0.0

    # Part du parcours effectivement conservée : 1 quand l'actif finit sur un
    # extrême, 0 quand il revient exactement d'où il est parti.
    retention = abs(var_24h) / amplitude_pct if amplitude_pct > 0 else 0.0

    # Volume des 24 h rapporté à une journée ordinaire, sur la même fenêtre que
    # l'amplitude.
    #
    # La version précédente comparait la *dernière bougie* à une médiane de
    # bougies. La dernière bougie horaire est en cours : selon qu'on scanne à
    # h+05 ou à h+55, elle contient cinq minutes ou cinquante-cinq minutes de
    # volume. BNB, en pleine cassure sur un volume de 5,7 fois la normale,
    # ressortait ainsi à 0,2 — l'indicateur mesurait l'heure qu'il est, pas le
    # marché.
    #
    # La médiane plutôt que la moyenne : une seule journée de capitulation
    # suffit à doubler une moyenne sur trente points, et l'anomalie qu'on
    # cherche à détecter deviendrait sa propre référence.
    volume_rel = 0.0
    if "volume" in df.columns and isinstance(df.index, pd.DatetimeIndex):
        vol = df["volume"]
        journee = float(vol.loc[vol.index >= vol.index[-1] - pd.Timedelta(hours=24)].sum())
        glissant = vol.rolling(24, min_periods=12).sum().dropna()
        med = float(glissant.median()) if len(glissant) >= 24 * 7 else 0.0
        if med > 0 and np.isfinite(med) and np.isfinite(journee):
            volume_rel = journee / med

    # Étiquette lisible. Les seuils sont en multiples d'ATR pour rester
    # comparables d'un actif à l'autre ; ils décrivent le parcours accompli,
    # ils n'annoncent pas sa suite.
    sens = "hausse" if var_24h > 0 else "baisse"

    # Un parcours ample dont il ne reste presque rien n'est pas un mouvement
    # directionnel : c'est une secousse. Elle se dit avant le sens, parce que
    # le sens n'y veut plus dire grand-chose.
    rendu = ampleur >= 1.2 and retention < 0.35

    if not amplitude_ref:
        etat = sens if abs(var_24h) > 0.05 else "journée calme"
    elif ampleur < 0.7:
        etat = "journée calme"
    elif rendu:
        etat = "secousse sans direction"
    elif ampleur < 1.4:
        etat = f"{sens} ordinaire"
    elif ampleur < 2.2:
        etat = f"{sens} marquée"
    else:
        etat = f"{sens} violente"

    return {
        "disponible": True,
        "var_1h": None if var_1h is None else round(var_1h, 2),
        "var_24h": round(var_24h, 2),
        "var_7j": None if var_7j is None else round(var_7j, 2),
        "amplitude_pct": round(amplitude_pct, 2),
        "amplitude_ref_pct": None if not amplitude_ref else round(amplitude_ref, 2),
        "ampleur": round(ampleur, 2),
        "retention": round(retention, 3),
        "position_range": round(position, 3),
        "volume_rel": round(volume_rel, 2),
        "etat": etat,
        "rendu": rendu,
    }


def analyze(asset: Asset, df: pd.DataFrame, *, timeframe: str = "1h",
            df_htf: pd.DataFrame | None = None,
            news_score: float = 0.0, news_count: int = 0,
            generated_at: str = "") -> Signal:
    """Analyse complète d'un actif et production du signal."""
    warnings: list[str] = []
    if len(df) < 60:
        warnings.append(f"historique court ({len(df)} bougies), fiabilité réduite")

    regime = S.detect_regime(df)
    weights = WEIGHTS[regime.name]

    factors = [
        factor_trend(df),
        factor_momentum(df),
        factor_mean_reversion(df),
        factor_volume(df),
        factor_breakout(df),
        factor_structure(df),
        factor_sentiment(news_score, news_count),
    ]
    for f in factors:
        f.weight = weights[f.name]

    # Somme pondérée, normalisée par la somme des poids en valeur absolue
    # (certains poids sont négatifs : mean_reversion en tendance).
    raw = sum(f.contribution for f in factors)
    norm = sum(abs(w) for w in weights.values()) or 1.0
    weighted = float(np.clip(raw / norm, -1.0, 1.0))
    # Score avant prise en compte du régime et de l'unité supérieure, conservé
    # pour l'audit : il isole ce que disent les seuls facteurs.
    raw_score = float(np.tanh(weighted / SCORE_SCALE)) * 100.0

    bias, bias_detail = htf_bias(df_htf)
    # Accord avec l'unité supérieure : bonus si aligné, forte pénalité si opposé.
    if bias != 0 and raw_score != 0:
        aligned = math.copysign(1.0, bias) == math.copysign(1.0, raw_score)
        htf_mult = 1.0 + 0.15 * abs(bias) if aligned else 1.0 - 0.35 * abs(bias)
        if not aligned:
            warnings.append(f"signal contraire à l'unité de temps supérieure ({bias_detail})")
    else:
        htf_mult = 1.0

    # Les pénalités s'appliquent à la somme pondérée, AVANT le calibrage, et
    # non au score déjà calibré. Les appliquer après compresserait deux fois :
    # le tanh sature d'abord, puis les multiplicateurs rabotent, si bien qu'un
    # score brut de 74 ressortait à 48 et qu'aucun seuil calibré ne signifiait
    # plus rien. Les points d'ancrage de SCORE_SCALE portent donc sur la
    # somme pondérée corrigée, ce qui est le sens voulu : « une confluence de
    # 0.24, une fois tenu compte du régime et de l'unité supérieure ».
    adjusted = weighted * regime.confidence_mult * htf_mult
    score_signed = float(np.clip(np.tanh(adjusted / SCORE_SCALE) * 100.0, -100.0, 100.0))

    # Le biais existe dès qu'il y a une orientation, même faible : c'est lui
    # qui alimente la liste de surveillance. La direction, elle, n'est
    # renseignée que si le signal est réellement déclenchable.
    bias = "long" if score_signed > 0 else "short" if score_signed < 0 else "neutre"
    direction = bias if abs(score_signed) >= SETTINGS.signal_threshold else "neutre"

    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    atr_pct = (atr_v / price * 100.0) if price > 0 and np.isfinite(atr_v) else 0.0
    if atr_pct > 12:
        warnings.append(f"volatilité extrême (ATR = {atr_pct:.1f} % du prix)")
    if regime.vol_percentile > 0.95:
        warnings.append("volatilité au plus haut de son historique, glissement probable")

    # Le plan est calculé sur le biais, pas sur la direction : un actif sous le
    # seuil conserve ainsi des niveaux affichables en liste de surveillance,
    # au lieu de n'exposer que des zéros.
    plan = _build_plan(asset, df, bias, abs(score_signed), regime)
    entry, stop, target, rr = plan.entry, plan.stop, plan.target, plan.rr

    # Un plan à espérance négative est rejeté même si la conviction est forte :
    # cela signifie que la structure ne laisse aucun objectif atteignable avant
    # un obstacle solide. Prendre le trade quand même reviendrait à ignorer la
    # seule information qui porte sur le résultat plutôt que sur la direction.
    if direction != "neutre" and plan.expected_r < MIN_EXPECTED_R:
        warnings.append(
            f"écarté : espérance {plan.expected_r:+.3f} R sous le minimum "
            f"({MIN_EXPECTED_R:+.2f} R) — {plan.target_basis}")
        direction = "neutre"

    actionable = direction != "neutre"

    return Signal(
        symbol=asset.symbol, label=asset.label, klass=asset.klass,
        direction=direction, bias=bias, actionable=actionable,
        score=round(abs(score_signed), 2),
        raw_score=round(raw_score, 2), price=round(price, 8),
        regime=regime.to_dict(), factors=[f.to_dict() for f in factors],
        entry=entry, stop=stop, target=target, rr=rr,
        atr=round(atr_v, 8) if np.isfinite(atr_v) else 0.0,
        atr_pct=round(atr_pct, 2), timeframe=timeframe,
        htf_alignment=bias, news_score=round(float(news_score), 3),
        news_count=news_count, generated_at=generated_at, warnings=warnings,
        win_prob=plan.win_prob, expected_r=plan.expected_r,
        stop_basis=plan.stop_basis, target_basis=plan.target_basis,
        stop_strength=plan.stop_strength, obstacle=plan.obstacle,
        plan_alternatives=plan.alternatives,
        mouvement=mouvement(df),
    )


def _build_plan(asset: Asset, df: pd.DataFrame, direction: str, score: float,
                regime) -> "L.Plan":
    """Construit le plan de trade optimal pour cet actif.

    Les niveaux ne sont plus déduits d'un multiple d'ATR fixe : ils sont
    adossés à la structure réellement observée (pivots, congestions,
    Fibonacci, point de contrôle du volume), et le couple retenu est celui qui
    maximise l'espérance mathématique. Les préréglages par classe d'actif
    servent de repli quand la structure est inexploitable.

    Le stop reste calé sur la volatilité, jamais sur un pourcentage fixe : un
    stop à 2 % est absurde sur un actif qui varie de 8 % par jour, et
    étouffant sur une paire forex qui varie de 0.4 %.
    """
    profile = RISK.get(asset.klass, RISK["crypto"])

    # Le plan fixe est le mode par défaut, et ce choix vient d'une mesure.
    #
    # Comparés sur le même historique, l'optimiseur de niveaux et un plan fixe
    # à 2 ATR / 2 R donnent :
    #
    #                        optimiseur   plan fixe
    #   trades                      340          91
    #   taux de réussite         27.9 %      40.7 %
    #   espérance réalisée      +0.045 R    +0.186 R
    #   facteur de profit          1.073       1.309
    #   drawdown maximal          29.3 R     11.45 R
    #
    # Le mécanisme est identifié. L'optimiseur retient un R/R de 3.0 dans 175
    # cas sur 340, or c'est précisément la bande qui perd : les R/R de 2.5 à
    # 3.5 réalisent 19.3 % de réussite et -0.029 R, quand la bande 1.5-2.5
    # réalise +0.277 R. Son stop moyen atteint 3.22 ATR contre 1.99, et 12 %
    # de ses trades expirent sans jamais toucher leur objectif, contre 0 %.
    #
    # La cause tient à sa fonction objectif : maximiser une espérance estimée
    # revient à retenir les estimations les plus flatteuses, qui sont aussi
    # les plus bruitées. Un optimiseur nourri d'une estimation imparfaite
    # sélectionne son erreur. Le plan fixe ne peut pas tromper son propre
    # estimateur, et c'est exactement ce qui le protège.
    #
    # L'optimiseur reste accessible pour la recherche
    # (`JIMBOT_PLAN_MODE=optimise`), de même que toute la détection de
    # structure, qui garde sa valeur descriptive dans les rapports.
    if _env("JIMBOT_PLAN_MODE", "fixe") == "fixe":
        # Paramètres surchargeables uniquement pour le contrôle de robustesse :
        # vérifier que le résultat ne tient pas à un choix particulier.
        return L.fixed_plan(
            df, direction, score,
            atr_mult=float(_env("JIMBOT_FIXED_ATR", str(profile.atr_stop_mult))),
            rr=float(_env("JIMBOT_FIXED_RR", str(profile.rr_target))),
            klass=asset.klass, regime_quality=float(regime.quality),
            symbol=asset.symbol)

    return L.optimal_plan(
        df, direction, score,
        regime_quality=float(regime.quality),
        fallback_atr_mult=profile.atr_stop_mult,
        fallback_rr=profile.rr_target,
        klass=asset.klass,
        symbol=asset.symbol,
    )
