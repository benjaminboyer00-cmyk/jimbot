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


# --------------------------------------------------------------------------
# Canaux adaptatifs et suiveurs de tendance
# --------------------------------------------------------------------------
def keltner(high: pd.Series, low: pd.Series, close: pd.Series,
            n: int = 20, mult: float = 2.0):
    """Canaux de Keltner : EMA encadrée par des multiples d'ATR.

    Différence avec Bollinger : la largeur suit l'amplitude vraie et non
    l'écart-type des clôtures, donc les gaps et les mèches y sont pris en
    compte. Le croisement des deux (« squeeze ») est le signal de compression
    le plus fiable.
    """
    mid = ema(close, n)
    band = mult * atr(high, low, close, n)
    return mid - band, mid, mid + band


def squeeze(high: pd.Series, low: pd.Series, close: pd.Series,
            n: int = 20) -> pd.Series:
    """Compression : bandes de Bollinger contenues dans les canaux de Keltner.

    Situation d'énergie potentielle — la volatilité est anormalement basse et
    revient statistiquement vers sa moyenne. Le signal ne dit rien de la
    direction, seulement qu'un mouvement approche.
    """
    bb_low, _, bb_up, _, _ = bollinger(close, n, 2.0)
    kc_low, _, kc_up = keltner(high, low, close, n, 1.5)
    return (bb_up < kc_up) & (bb_low > kc_low)


def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               n: int = 10, mult: float = 3.0):
    """Supertrend : bande de retournement suiveuse. Renvoie (ligne, sens).

    Le sens vaut +1 en tendance haussière et -1 en tendance baissière. La
    bande ne recule jamais tant que la tendance tient, ce qui en fait un stop
    suiveur naturel.
    """
    atr_v = atr(high, low, close, n)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * atr_v
    lower = hl2 - mult * atr_v

    up_arr = upper.to_numpy(dtype=float).copy()
    lo_arr = lower.to_numpy(dtype=float).copy()
    c = close.to_numpy(dtype=float)
    direction = np.ones(len(c))

    for i in range(1, len(c)):
        if not np.isfinite(up_arr[i]) or not np.isfinite(up_arr[i - 1]):
            continue
        # Les bandes se resserrent mais ne s'élargissent jamais dans le sens
        # de la tendance : c'est ce qui empêche le stop de reculer.
        if c[i - 1] <= up_arr[i - 1]:
            up_arr[i] = min(up_arr[i], up_arr[i - 1])
        if c[i - 1] >= lo_arr[i - 1]:
            lo_arr[i] = max(lo_arr[i], lo_arr[i - 1])

        if c[i] > up_arr[i - 1]:
            direction[i] = 1.0
        elif c[i] < lo_arr[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]

    line = np.where(direction > 0, lo_arr, up_arr)
    return (pd.Series(line, index=close.index),
            pd.Series(direction, index=close.index))


def chandelier_exit(high: pd.Series, low: pd.Series, close: pd.Series,
                    n: int = 22, mult: float = 3.0):
    """Sortie chandelier : ancrée au plus-haut atteint depuis l'entrée.

    Contrairement à un stop suiveur classique fondé sur le prix courant,
    celui-ci part de l'extrême atteint, donc il ne se resserre pas lors d'un
    simple repli — il laisse respirer la position.
    """
    atr_v = mult * atr(high, low, close, n)
    return (high.rolling(n, min_periods=n).max() - atr_v,
            low.rolling(n, min_periods=n).min() + atr_v)


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series,
             conv: int = 9, base: int = 26, span_b: int = 52):
    """Ichimoku. Renvoie (tenkan, kijun, senkou A, senkou B, chikou).

    L'intérêt principal est le nuage : il donne une zone de support ou de
    résistance projetée dans le futur, ce qu'aucune moyenne mobile ne fait.
    """
    def mid(n: int) -> pd.Series:
        return (high.rolling(n, min_periods=n).max()
                + low.rolling(n, min_periods=n).min()) / 2.0

    tenkan, kijun = mid(conv), mid(base)
    return (tenkan, kijun,
            ((tenkan + kijun) / 2.0).shift(base),
            mid(span_b).shift(base),
            close.shift(-base))


# --------------------------------------------------------------------------
# Tests statistiques de structure
# --------------------------------------------------------------------------
def choppiness(high: pd.Series, low: pd.Series, close: pd.Series,
               n: int = 14) -> pd.Series:
    """Indice de choppiness, dans [0, 100].

    Compare la somme des amplitudes vraies au parcours net réalisé. Proche de
    100, le marché s'agite sans avancer ; proche de 0, il progresse en ligne
    droite. Complémentaire de l'ADX, qui mesure la force et non l'efficacité.
    """
    tr_sum = true_range(high, low, close).rolling(n, min_periods=n).sum()
    span = (high.rolling(n, min_periods=n).max()
            - low.rolling(n, min_periods=n).min()).replace(0.0, np.nan)
    ratio = (tr_sum / span).replace(0.0, np.nan)
    return 100.0 * np.log10(ratio) / np.log10(n)


def variance_ratio(close: pd.Series, q: int = 4, n: int = 120) -> pd.Series:
    """Test du ratio de variance de Lo et MacKinlay.

    Sous l'hypothèse de marche aléatoire, la variance des rendements agrégés
    sur q périodes vaut exactement q fois la variance des rendements
    unitaires, donc le ratio vaut 1.

    - VR > 1 : les rendements s'enchaînent dans le même sens (persistance)
    - VR < 1 : ils alternent (retour à la moyenne)

    C'est un test statistique en bonne et due forme, là où l'exposant de
    Hurst n'est qu'un estimateur biaisé sur échantillon court. Les deux se
    complètent : le Hurst pour l'ordre de grandeur, le VR pour le verdict.
    """
    r = np.log(close / close.shift(1))
    var_1 = r.rolling(n, min_periods=n).var(ddof=1)
    var_q = r.rolling(q).sum().rolling(n, min_periods=n).var(ddof=1)
    return (var_q / (q * var_1.replace(0.0, np.nan))).replace([np.inf, -np.inf], np.nan)


def money_flow_index(high: pd.Series, low: pd.Series, close: pd.Series,
                     volume: pd.Series, n: int = 14) -> pd.Series:
    """MFI : un RSI pondéré par le volume, dans [0, 100].

    Un excès de prix confirmé par le volume est plus significatif qu'un excès
    de prix seul — c'est toute la différence avec le RSI.
    """
    typical = (high + low + close) / 3.0
    flow = typical * volume
    delta = typical.diff()
    pos = flow.where(delta > 0, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(delta < 0, 0.0).rolling(n, min_periods=n).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + ratio))
    return out.where(neg != 0.0, 100.0).where(pos.notna())


def divergence(price: pd.Series, oscillator: pd.Series, *,
               lookback: int = 60, left: int = 3, right: int = 3) -> tuple[float, str]:
    """Divergence entre le prix et un oscillateur.

    Renvoie (intensité dans [-1, 1], description). Une divergence baissière —
    le prix inscrit un plus-haut supérieur alors que l'oscillateur inscrit un
    plus-haut inférieur — signale un essoufflement de l'impulsion avant que
    le prix ne se retourne. C'est l'un des rares signaux réellement avancés.
    """
    p = price.tail(lookback).to_numpy(dtype=float)
    o = oscillator.tail(lookback).to_numpy(dtype=float)
    if len(p) < left + right + 10 or not np.isfinite(o).any():
        return 0.0, "historique insuffisant"

    def pivots(arr: np.ndarray, high: bool) -> list[int]:
        out = []
        for i in range(left, len(arr) - right):
            w = arr[i - left: i + right + 1]
            if not np.isfinite(w).all():
                continue
            if (arr[i] == w.max() if high else arr[i] == w.min()) and (w == arr[i]).sum() == 1:
                out.append(i)
        return out

    for is_high in (True, False):
        idx = pivots(p, is_high)
        if len(idx) < 2:
            continue
        a, b = idx[-2], idx[-1]
        if not (np.isfinite(o[a]) and np.isfinite(o[b])):
            continue
        price_up = p[b] > p[a]
        osc_up = o[b] > o[a]
        if price_up == osc_up:
            continue  # prix et oscillateur d'accord : pas de divergence

        # L'intensité mesure l'écart relatif de l'oscillateur entre les deux
        # pivots, rapportée à son amplitude sur la fenêtre.
        span = np.nanmax(o) - np.nanmin(o)
        strength = min(1.0, abs(o[b] - o[a]) / span) if span > 0 else 0.0
        if is_high and price_up and not osc_up:
            return -strength, "divergence baissière : plus-haut du prix non confirmé"
        if not is_high and not price_up and osc_up:
            return strength, "divergence haussière : plus-bas du prix non confirmé"
    return 0.0, "aucune divergence"
