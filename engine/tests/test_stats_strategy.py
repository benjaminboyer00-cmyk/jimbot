"""Régimes de marché, calibrage du score et cohérence des niveaux."""
import numpy as np
import pandas as pd
import pytest

from jimbot import stats as S
from jimbot import strategy as St
from jimbot.config import UNIVERSE


# --------------------------------------------------------------------------
# Statistiques
# --------------------------------------------------------------------------
def test_hurst_distingue_retour_a_la_moyenne_et_marche_aleatoire(random_walk, ranging):
    """Le test qui compte : l'estimateur R/S doit séparer les deux régimes.

    Une estimation naïve par écart-type des différences échoue ici dès qu'il
    y a une dérive — c'est la raison du passage à l'analyse R/S.
    """
    h_rw = S.hurst_exponent(random_walk["close"])
    h_ou = S.hurst_exponent(ranging["close"])
    assert h_ou < h_rw, "un processus à retour à la moyenne doit avoir un H plus faible"
    assert h_ou < 0.45


def test_hurst_insensible_a_la_derive():
    """Ajouter une tendance à une marche aléatoire ne doit pas changer H.

    C'est précisément ce que l'ancienne implémentation ratait.
    """
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.01, 600)
    sans = pd.Series(100 * np.exp(np.cumsum(noise)))
    avec = pd.Series(100 * np.exp(np.cumsum(noise + 0.004)))
    assert S.hurst_exponent(sans) == pytest.approx(S.hurst_exponent(avec), abs=0.06)


def test_hurst_renvoie_nan_si_historique_trop_court():
    assert np.isnan(S.hurst_exponent(pd.Series([1.0, 2.0, 3.0])))


def test_regimes_correctement_classes(trending, ranging):
    assert S.detect_regime(trending).name == "tendance_haussière"
    assert S.detect_regime(ranging).name in {"range", "chaotique"}


def test_regime_baissier_detecte():
    rng = np.random.default_rng(11)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(-0.003, 0.008, 400))),
                  index=pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC"))
    df = pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996,
                       "close": c, "volume": np.ones(400)})
    assert S.detect_regime(df).name == "tendance_baissière"


def test_multiplicateur_de_confiance_n_amplifie_jamais(trending, ranging, random_walk):
    """Le multiplicateur doit atténuer un signal, jamais le gonfler."""
    for df in (trending, ranging, random_walk):
        assert 0.3 <= S.detect_regime(df).confidence_mult <= 1.0


def test_max_drawdown():
    equity = pd.Series([100, 120, 90, 95, 130])
    # Pic à 120, creux à 90 -> -25 %
    assert S.max_drawdown(equity) == pytest.approx(0.25)
    assert S.max_drawdown(pd.Series([100, 110, 120])) == pytest.approx(0.0)


def test_sharpe_nul_sur_serie_constante():
    assert S.sharpe(pd.Series([0.0] * 50)) == 0.0


def test_correlation_matrix(trending, ranging):
    corr = S.correlation_matrix({"A": trending["close"], "B": ranging["close"]}, n=60)
    assert corr.shape == (2, 2)
    assert corr.loc["A", "A"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Calibrage du score
# --------------------------------------------------------------------------
def test_calibrage_du_score_respecte_ses_points_d_ancrage():
    """Le score doit être réellement atteignable.

    Sans calibrage, la somme pondérée plafonnait autour de 0.30, soit un score
    de 30/100 : aucun seuil au-dessus de 30 n'aurait jamais pu se déclencher.
    """
    def score(weighted: float) -> float:
        return float(np.tanh(weighted / St.SCORE_SCALE)) * 100

    assert score(0.13) == pytest.approx(34.6, abs=1.5)   # lecture ordinaire
    assert score(0.24) == pytest.approx(58.3, abs=1.5)   # seuil de signal
    assert score(0.35) == pytest.approx(75.0, abs=1.5)   # confluence forte
    assert score(0.0) == pytest.approx(0.0)
    assert score(-0.24) == pytest.approx(-58.3, abs=1.5)  # symétrie


def test_score_est_monotone_et_borne():
    xs = np.linspace(-1, 1, 60)
    ys = [np.tanh(x / St.SCORE_SCALE) * 100 for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:])), "le score doit être monotone"
    assert max(abs(y) for y in ys) < 100


# --------------------------------------------------------------------------
# Analyse complète
# --------------------------------------------------------------------------
@pytest.fixture
def asset():
    return next(a for a in UNIVERSE if a.symbol == "BTC-USD")


def test_analyse_produit_un_signal_coherent(asset, trending):
    sig = St.analyze(asset, trending, timeframe="1h", generated_at="2026-01-01T00:00:00Z")
    assert 0 <= sig.score <= 100
    assert sig.direction in {"long", "short", "neutre"}
    assert len(sig.factors) == 6
    assert sig.price > 0


def test_niveaux_coherents_pour_un_achat(asset, trending):
    sig = St.analyze(asset, trending, timeframe="1h")
    if sig.direction == "long":
        assert sig.stop < sig.entry < sig.target
        assert sig.rr == pytest.approx(2.0, abs=0.01)


def test_niveaux_coherents_pour_une_vente(asset):
    rng = np.random.default_rng(21)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(-0.004, 0.008, 400))),
                  index=pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC"))
    df = pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996,
                       "close": c, "volume": np.ones(400) * 1000})
    sig = St.analyze(asset, df, timeframe="1h")
    if sig.direction == "short":
        assert sig.target < sig.entry < sig.stop
        assert sig.rr > 0


def test_aucun_niveau_sur_signal_neutre(asset, random_walk):
    sig = St.analyze(asset, random_walk, timeframe="1h")
    if sig.direction == "neutre":
        assert sig.stop == 0.0 and sig.target == 0.0


def test_les_poids_somment_a_un_par_regime():
    for regime, weights in St.WEIGHTS.items():
        total = sum(abs(w) for w in weights.values())
        assert total == pytest.approx(1.0, abs=0.01), f"régime {regime}"


def test_desaccord_avec_unite_superieure_penalise(asset, trending):
    """Un signal contraire à l'unité de temps supérieure doit être atténué."""
    rng = np.random.default_rng(31)
    baisse = pd.Series(100 * np.exp(np.cumsum(rng.normal(-0.01, 0.008, 200))),
                       index=pd.date_range("2026-01-01", periods=200, freq="4h", tz="UTC"))
    htf = pd.DataFrame({"open": baisse, "high": baisse * 1.004, "low": baisse * 0.996,
                        "close": baisse, "volume": np.ones(200)})
    seul = St.analyze(asset, trending, timeframe="1h")
    oppose = St.analyze(asset, trending, timeframe="1h", df_htf=htf)
    assert oppose.score < seul.score
    assert any("unité de temps supérieure" in w for w in oppose.warnings)


def test_historique_court_signale_dans_les_avertissements(asset, trending):
    sig = St.analyze(asset, trending.tail(50), timeframe="1h")
    assert any("historique court" in w for w in sig.warnings)


def test_facteur_volume_neutralise_sans_volume(asset, trending):
    """Le forex via Yahoo ne fournit pas de volume : le facteur doit s'annuler
    proprement au lieu de produire du bruit."""
    sans_volume = trending.copy()
    sans_volume["volume"] = 0.0
    f = St.factor_volume(sans_volume)
    assert f.value == 0.0
    assert "non disponible" in f.detail


def test_serialisation_json_du_signal(asset, trending):
    import json
    sig = St.analyze(asset, trending, timeframe="1h")
    json.dumps(sig.to_dict())  # doit passer sans type non sérialisable
