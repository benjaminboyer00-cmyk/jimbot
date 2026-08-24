"""Les indicateurs sont réimplémentés à la main : ils doivent être vérifiés
contre leurs propriétés mathématiques, pas seulement contre l'absence d'erreur.
"""
import numpy as np
import pandas as pd
import pytest

from jimbot import indicators as I


def test_rsi_reste_dans_ses_bornes(trending, ranging, random_walk):
    for df in (trending, ranging, random_walk):
        r = I.rsi(df["close"]).dropna()
        assert not r.empty
        assert r.between(0, 100).all(), "le RSI doit rester dans [0, 100]"


def test_rsi_vaut_100_sans_aucune_perte():
    """Cas limite classique : une division par zéro donnerait NaN au lieu de 100."""
    close = pd.Series(np.arange(1, 60, dtype=float))  # strictement croissant
    assert I.rsi(close).iloc[-1] == pytest.approx(100.0)


def test_rsi_tend_vers_zero_sans_aucun_gain():
    close = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert I.rsi(close).iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_ema_converge_vers_une_serie_constante():
    close = pd.Series([50.0] * 100)
    assert I.ema(close, 20).iloc[-1] == pytest.approx(50.0)


def test_atr_est_positif_et_borne_par_l_amplitude(trending):
    a = I.atr(trending["high"], trending["low"], trending["close"]).dropna()
    assert (a > 0).all()
    tr = I.true_range(trending["high"], trending["low"], trending["close"]).dropna()
    # Une moyenne lissée ne peut pas dépasser le maximum de sa série source.
    assert a.max() <= tr.max() * 1.01


def test_adx_et_di_restent_dans_leurs_bornes(trending, ranging):
    for df in (trending, ranging):
        adx, plus_di, minus_di = I.adx(df["high"], df["low"], df["close"])
        for s in (adx.dropna(), plus_di.dropna(), minus_di.dropna()):
            assert s.between(0, 100).all()


def test_adx_plus_eleve_en_tendance_qu_en_range(trending, ranging):
    a_trend = I.adx(trending["high"], trending["low"], trending["close"])[0].dropna().median()
    a_range = I.adx(ranging["high"], ranging["low"], ranging["close"])[0].dropna().median()
    assert a_trend > a_range


def test_bollinger_encadre_le_prix(trending):
    lower, mid, upper, pct_b, width = I.bollinger(trending["close"])
    valid = lower.notna()
    assert (lower[valid] <= mid[valid]).all()
    assert (mid[valid] <= upper[valid]).all()
    assert (width.dropna() > 0).all()


def test_slope_r2_detecte_une_droite_parfaite():
    """Sur une exponentielle pure, le log-prix est une droite : R² = 1."""
    close = pd.Series(100 * np.exp(np.arange(100) * 0.01))
    slope, r2 = I.slope_r2(close, 40)
    assert r2.iloc[-1] == pytest.approx(1.0, abs=1e-9)
    assert slope.iloc[-1] == pytest.approx(0.01, abs=1e-9)


def test_slope_negative_en_baisse():
    close = pd.Series(100 * np.exp(-np.arange(100) * 0.01))
    slope, _ = I.slope_r2(close, 40)
    assert slope.iloc[-1] < 0


def test_donchian_exclut_la_bougie_courante(trending):
    """Sans le décalage, le canal contiendrait le prix courant et aucune
    cassure ne pourrait jamais être détectée."""
    low, high = I.donchian(trending["high"], trending["low"], 20)
    # Le canal à l'instant t ne doit dépendre que des bougies < t.
    manual_high = trending["high"].iloc[-21:-1].max()
    assert high.iloc[-1] == pytest.approx(manual_high)


def test_volume_zscore_est_centre(trending):
    z = I.volume_zscore(trending["volume"], 30).dropna()
    assert abs(z.mean()) < 1.0
    assert z.std() > 0


def test_vwap_reste_dans_l_amplitude_des_prix(trending):
    v = I.vwap(trending["high"], trending["low"], trending["close"],
               trending["volume"], 20).dropna()
    assert (v >= trending["low"].min()).all()
    assert (v <= trending["high"].max()).all()


def test_macd_histogramme_est_la_difference():
    close = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 200))))
    line, signal, hist = I.macd(close)
    assert (hist.dropna() - (line - signal).dropna()).abs().max() < 1e-12


def test_les_indicateurs_ne_modifient_pas_leur_entree(trending):
    before = trending["close"].copy()
    I.rsi(trending["close"]); I.ema(trending["close"], 20)
    I.bollinger(trending["close"]); I.macd(trending["close"])
    pd.testing.assert_series_equal(before, trending["close"])
