"""Indicateurs techniques implémentés à la main (numpy/pandas uniquement).

Aucune dépendance à TA-Lib : tout est recalculé ici pour rester installable
partout et pour que chaque formule soit auditable.

Convention : toutes les fonctions prennent et renvoient des `pd.Series`
indexées par le temps, et ne modifient jamais leur entrée.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Moyennes
# --------------------------------------------------------------------------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Lissage de Wilder — utilisé par RSI, ATR et ADX (alpha = 1/n)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


# --------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI de Wilder. Renvoie une valeur dans [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    # Marché sans aucune perte sur la fenêtre -> RSI = 100 (et non NaN).
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Renvoie (ligne MACD, ligne signal, histogramme)."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               n: int = 14, d: int = 3):
    """Oscillateur stochastique %K / %D."""
    hh = high.rolling(n, min_periods=n).max()
    ll = low.rolling(n, min_periods=n).min()
    rng = (hh - ll).replace(0.0, np.nan)
    k = 100.0 * (close - ll) / rng
    return k, k.rolling(d, min_periods=d).mean()


def roc(close: pd.Series, n: int) -> pd.Series:
    """Rate of change en pourcentage sur n périodes."""
    return 100.0 * (close / close.shift(n) - 1.0)


# --------------------------------------------------------------------------
# Volatilité
# --------------------------------------------------------------------------
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                     axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    """Renvoie (bande basse, moyenne, bande haute, %B, largeur relative)."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return lower, mid, upper, pct_b, width


def realized_vol(close: pd.Series, n: int = 20, periods_per_year: int = 365) -> pd.Series:
    """Volatilité réalisée annualisée à partir des rendements log."""
    r = np.log(close / close.shift(1))
    return r.rolling(n, min_periods=n).std(ddof=0) * np.sqrt(periods_per_year)


# --------------------------------------------------------------------------
# Tendance
# --------------------------------------------------------------------------
def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14):
    """ADX de Wilder. Renvoie (ADX, +DI, -DI).

    ADX > 25 : tendance établie. ADX < 20 : marché en range.
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    tr_n = wilder(true_range(high, low, close), n)
    tr_safe = tr_n.replace(0.0, np.nan)
    plus_di = 100.0 * wilder(plus_dm, n) / tr_safe
    minus_di = 100.0 * wilder(minus_dm, n) / tr_safe

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return wilder(dx, n), plus_di, minus_di


def donchian(high: pd.Series, low: pd.Series, n: int = 20):
    """Canal de Donchian (bornes exclues de la bougie courante)."""
    return low.rolling(n, min_periods=n).min().shift(1), \
           high.rolling(n, min_periods=n).max().shift(1)


def slope_r2(close: pd.Series, n: int = 30):
    """Régression linéaire glissante sur log(prix).

    Renvoie (pente annualisée en % par période, R²). Le R² mesure la
    « propreté » de la tendance : une pente forte avec un R² faible est du
    bruit, pas une tendance exploitable.
    """
    y_all = np.log(close.to_numpy(dtype=float))
    x = np.arange(n, dtype=float)
    x_c = x - x.mean()
    denom_x = float((x_c ** 2).sum())

    slopes = np.full(len(y_all), np.nan)
    r2s = np.full(len(y_all), np.nan)
    for i in range(n - 1, len(y_all)):
        y = y_all[i - n + 1: i + 1]
        if not np.isfinite(y).all():
            continue
        y_c = y - y.mean()
        beta = float((x_c * y_c).sum()) / denom_x
        ss_tot = float((y_c ** 2).sum())
        ss_res = float(((y_c - beta * x_c) ** 2).sum())
        slopes[i] = beta
        r2s[i] = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return (pd.Series(slopes, index=close.index),
            pd.Series(r2s, index=close.index))


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def volume_zscore(volume: pd.Series, n: int = 30) -> pd.Series:
    """Écart du volume courant à sa moyenne, en écarts-types."""
    mu = volume.rolling(n, min_periods=n).mean()
    sd = volume.rolling(n, min_periods=n).std(ddof=0).replace(0.0, np.nan)
    return (volume - mu) / sd


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, n: int = 20) -> pd.Series:
    """VWAP glissant sur n bougies (prix typique pondéré par le volume)."""
    tp = (high + low + close) / 3.0
    pv = (tp * volume).rolling(n, min_periods=n).sum()
    v = volume.rolling(n, min_periods=n).sum().replace(0.0, np.nan)
    return pv / v
