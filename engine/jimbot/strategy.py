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
from . import stats as S
from .config import Asset, RISK, SETTINGS


# Constante de calibrage du score.
#
# La somme pondérée des six facteurs est bornée dans [-1, +1] par construction,
# mais elle n'atteint jamais ces bornes en pratique : il faudrait que tous les
# facteurs saturent simultanément dans le même sens. Mesuré sur l'univers
# suivi, sa valeur absolue moyenne est ~0.13 et son maximum ~0.30. Laissée
# telle quelle et multipliée par 100, elle produirait un score plafonnant
# autour de 30, sur lequel aucun seuil au-dessus de 30 ne pourrait jamais se
# déclencher.
#
# On applique donc une compression tanh calibrée pour que :
#   - une lecture ordinaire      (0.13) donne ~35/100
#   - une confluence nette       (0.24) donne ~58/100, le seuil de signal
#   - une confluence exceptionnelle (0.35) donne ~75/100
# tanh conserve la monotonie et sature progressivement : un score de 95
# reste atteignable mais exige un alignement quasi total des six facteurs.
SCORE_SCALE = 0.36

# --------------------------------------------------------------------------
# Pondérations par régime : la même donnée ne vaut pas la même chose partout.
# Chaque colonne somme à 1.0.
# --------------------------------------------------------------------------
WEIGHTS: dict[str, dict[str, float]] = {
    "tendance_haussière": {
        "trend": 0.32, "momentum": 0.24, "breakout": 0.18,
        "volume": 0.12, "mean_reversion": -0.04, "sentiment": 0.10,
    },
    "tendance_baissière": {
        "trend": 0.32, "momentum": 0.24, "breakout": 0.18,
        "volume": 0.12, "mean_reversion": -0.04, "sentiment": 0.10,
    },
    "range": {
        "trend": 0.08, "momentum": 0.10, "breakout": 0.05,
        "volume": 0.12, "mean_reversion": 0.50, "sentiment": 0.15,
    },
    "chaotique": {
        "trend": 0.18, "momentum": 0.18, "breakout": 0.10,
        "volume": 0.18, "mean_reversion": 0.18, "sentiment": 0.18,
    },
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
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def actionable(self) -> bool:
        return self.direction != "neutre" and self.score >= SETTINGS.signal_threshold


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
        factor_sentiment(news_score, news_count),
    ]
    for f in factors:
        f.weight = weights[f.name]

    # Somme pondérée, normalisée par la somme des poids en valeur absolue
    # (certains poids sont négatifs : mean_reversion en tendance).
    raw = sum(f.contribution for f in factors)
    norm = sum(abs(w) for w in weights.values()) or 1.0
    weighted = float(np.clip(raw / norm, -1.0, 1.0))
    # Puis calibrage sur l'échelle 0-100 : voir SCORE_SCALE.
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

    score_signed = raw_score * regime.confidence_mult * htf_mult
    score_signed = float(np.clip(score_signed, -100.0, 100.0))

    if abs(score_signed) < SETTINGS.signal_threshold:
        direction = "neutre"
    else:
        direction = "long" if score_signed > 0 else "short"

    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    atr_pct = (atr_v / price * 100.0) if price > 0 and np.isfinite(atr_v) else 0.0
    if atr_pct > 12:
        warnings.append(f"volatilité extrême (ATR = {atr_pct:.1f} % du prix)")
    if regime.vol_percentile > 0.95:
        warnings.append("volatilité au plus haut de son historique, glissement probable")

    entry, stop, target, rr = _levels(asset, price, atr_v, direction)

    return Signal(
        symbol=asset.symbol, label=asset.label, klass=asset.klass,
        direction=direction, score=round(abs(score_signed), 2),
        raw_score=round(raw_score, 2), price=round(price, 8),
        regime=regime.to_dict(), factors=[f.to_dict() for f in factors],
        entry=entry, stop=stop, target=target, rr=rr,
        atr=round(atr_v, 8) if np.isfinite(atr_v) else 0.0,
        atr_pct=round(atr_pct, 2), timeframe=timeframe,
        htf_alignment=bias, news_score=round(float(news_score), 3),
        news_count=news_count, generated_at=generated_at, warnings=warnings,
    )


def _levels(asset: Asset, price: float, atr_v: float,
            direction: str) -> tuple[float, float, float, float]:
    """Calcule entrée, stop et objectif à partir de l'ATR.

    Le stop est placé à N ATR, pas à un pourcentage fixe : un stop à 2 % est
    absurde sur un actif qui bouge de 8 % par jour, et étouffant sur une paire
    forex qui bouge de 0.4 %.
    """
    profile = RISK.get(asset.klass, RISK["crypto"])
    if direction == "neutre" or not np.isfinite(atr_v) or atr_v <= 0 or price <= 0:
        return round(price, 8), 0.0, 0.0, 0.0

    dist = profile.atr_stop_mult * atr_v
    if direction == "long":
        stop, target = price - dist, price + dist * profile.rr_target
    else:
        stop, target = price + dist, price - dist * profile.rr_target

    risk = abs(price - stop)
    reward = abs(target - price)
    rr = reward / risk if risk > 0 else 0.0
    return round(price, 8), round(max(stop, 0.0), 8), round(max(target, 0.0), 8), round(rr, 2)
