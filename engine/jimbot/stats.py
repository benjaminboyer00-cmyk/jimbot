"""Statistiques de marché : régimes, mémoire longue, corrélations, qualité.

Cette couche répond à une question que les indicateurs classiques n'adressent
pas : « dans quel type de marché suis-je ? ». Un signal de suivi de tendance
n'a aucune valeur en régime de retour à la moyenne, et inversement.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import indicators as I


def hurst_exponent(close: pd.Series, min_window: int = 8, max_windows: int = 6) -> float:
    """Exposant de Hurst par analyse R/S (rescaled range) de Mandelbrot.

    Pour chaque taille de fenêtre n, la série de rendements est découpée en
    blocs disjoints ; dans chaque bloc on retire la moyenne locale (ce qui
    neutralise la dérive du marché — une estimation naïve sur les écarts-types
    de différences donne un résultat faux dès qu'il y a une tendance), puis on
    mesure l'étendue de la déviation cumulée rapportée à l'écart-type.
    On régresse ensuite log(R/S) sur log(n).

    Attention au biais : l'estimateur R/S surestime H sur les échantillons
    courts (biais d'Anis-Lloyd). Mesuré sur 200 marches aléatoires de 400
    bougies, il rend H ≈ 0.57 alors que la valeur théorique est 0.50. La
    référence neutre à utiliser est donc ~0.57, pas 0.50 :
    - H < 0.45 : anti-persistance nette, retour à la moyenne
    - H ≈ 0.55-0.60 : indiscernable d'une marche aléatoire
    - H > 0.65 : persistance réelle, la tendance a tendance à se prolonger
    À lire en comparaison (entre actifs, ou dans le temps), pas en absolu.
    Renvoie NaN si l'historique est trop court pour une estimation crédible.
    """
    r = np.log(close / close.shift(1)).dropna().to_numpy(dtype=float)
    r = r[np.isfinite(r)]
    n_obs = len(r)
    if n_obs < min_window * 4:
        return float("nan")

    # Tailles de fenêtre en progression géométrique, jusqu'à n_obs/2.
    max_window = n_obs // 2
    if max_window <= min_window:
        return float("nan")
    windows = np.unique(np.geomspace(min_window, max_window,
                                     num=max_windows).astype(int))
    windows = windows[windows >= min_window]

    xs, ys = [], []
    for w in windows:
        n_blocks = n_obs // w
        if n_blocks < 1:
            continue
        rs_vals = []
        for b in range(n_blocks):
            block = r[b * w:(b + 1) * w]
            sd = float(block.std(ddof=0))
            if sd <= 0:
                continue
            dev = np.cumsum(block - block.mean())
            rng = float(dev.max() - dev.min())
            if rng > 0:
                rs_vals.append(rng / sd)
        if rs_vals:
            xs.append(np.log(w))
            ys.append(np.log(float(np.mean(rs_vals))))

    if len(xs) < 3:
        return float("nan")
    beta = np.polyfit(xs, ys, 1)[0]
    return float(np.clip(beta, 0.0, 1.0))


def zscore(s: pd.Series, n: int = 50) -> pd.Series:
    """Z-score glissant : combien d'écarts-types au-dessus de la moyenne."""
    mu = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0).replace(0.0, np.nan)
    return (s - mu) / sd


def max_drawdown(equity: pd.Series) -> float:
    """Drawdown maximal en fraction (0.25 = -25 %)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak.replace(0.0, np.nan)) - 1.0
    return float(abs(dd.min())) if dd.notna().any() else 0.0


def sharpe(returns: pd.Series, periods_per_year: int = 365) -> float:
    """Sharpe annualisé, taux sans risque supposé nul."""
    r = returns.dropna()
    if len(r) < 3:
        return 0.0
    sd = float(r.std(ddof=1))
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: int = 365) -> float:
    """Sortino : ne pénalise que la volatilité baissière."""
    r = returns.dropna()
    if len(r) < 3:
        return 0.0
    downside = r[r < 0]
    dd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    if dd == 0:
        return 0.0
    return float(r.mean() / dd * np.sqrt(periods_per_year))


@dataclass
class Regime:
    """Diagnostic du régime de marché courant."""

    name: str            # "tendance_haussière" | "tendance_baissière" | "range" | "chaotique"
    trend_strength: float  # ADX normalisé 0-1
    hurst: float
    vol_percentile: float  # percentile de la volatilité courante sur l'historique
    quality: float         # R² de la régression, 0-1
    # Multiplicateur appliqué au score, dans [0.3, 1.0] : n'atténue jamais
    # au-delà, et ne gonfle jamais un signal (plafonné à 1.0).
    confidence_mult: float

    def to_dict(self) -> dict:
        return asdict(self)


def detect_regime(df: pd.DataFrame) -> Regime:
    """Classe le marché à partir de l'ADX, du Hurst, de la pente et de la vol.

    `df` doit contenir les colonnes open/high/low/close/volume.
    """
    close, high, low = df["close"], df["high"], df["low"]

    adx_series, plus_di, minus_di = I.adx(high, low, close)
    adx_v = _last(adx_series, 0.0)
    slope_s, r2_s = I.slope_r2(close, 40)
    slope, r2 = _last(slope_s, 0.0), _last(r2_s, 0.0)
    h = hurst_exponent(close)

    vol = I.realized_vol(close, 20)
    vol_now = _last(vol, float("nan"))
    vol_hist = vol.dropna()
    vol_pct = (float((vol_hist < vol_now).mean())
               if len(vol_hist) > 20 and np.isfinite(vol_now) else 0.5)

    trend_strength = float(np.clip(adx_v / 50.0, 0.0, 1.0))
    directional = _last(plus_di, 0.0) - _last(minus_di, 0.0)

    # Deux façons d'être en tendance, pour éviter une zone morte entre les
    # seuils : soit la trajectoire est propre (R² élevé) même avec un ADX
    # modéré, soit elle est fortement directionnelle (ADX élevé) même si
    # elle est plus bruitée.
    clean_trend = r2 >= 0.55 and adx_v >= 15.0
    strong_trend = adx_v >= 25.0 and r2 >= 0.30
    trending = clean_trend or strong_trend
    up = slope > 0 if np.isfinite(slope) and slope != 0 else directional > 0

    if trending:
        name = "tendance_haussière" if up else "tendance_baissière"
    elif adx_v < 20.0 and r2 < 0.45:
        name = "range"
    else:
        name = "chaotique"

    # Volatilité extrême = stops sautés, exécution dégradée : on réduit la confiance.
    vol_penalty = 0.75 if vol_pct > 0.92 else (0.9 if vol_pct > 0.8 else 1.0)
    base = {"tendance_haussière": 1.0, "tendance_baissière": 1.0,
            "range": 0.85, "chaotique": 0.6}[name]
    quality_bonus = 0.85 + 0.3 * float(np.clip(r2, 0.0, 1.0))

    return Regime(
        name=name,
        trend_strength=round(trend_strength, 3),
        hurst=round(h, 3) if np.isfinite(h) else float("nan"),
        vol_percentile=round(vol_pct, 3),
        quality=round(float(r2), 3),
        confidence_mult=round(float(np.clip(base * vol_penalty * quality_bonus, 0.3, 1.0)), 3),
    )


def correlation_matrix(closes: dict[str, pd.Series], n: int = 60) -> pd.DataFrame:
    """Corrélation des rendements log sur les n dernières bougies communes.

    Sert à détecter la concentration du risque : trois positions longues
    corrélées à 0.9 constituent une seule position de taille triple.
    """
    rets = {}
    for sym, s in closes.items():
        r = np.log(s / s.shift(1)).dropna()
        if len(r) >= n // 2:
            rets[sym] = r.tail(n)
    if len(rets) < 2:
        return pd.DataFrame()
    return pd.DataFrame(rets).corr(min_periods=max(10, n // 4))


def _last(s: pd.Series, default: float = float("nan")) -> float:
    """Dernière valeur finie d'une série, sinon `default`."""
    s = s.dropna()
    if s.empty:
        return default
    v = float(s.iloc[-1])
    return v if np.isfinite(v) else default
