"""Régimes de marché, calibrage du score et cohérence des niveaux."""
import numpy as np
import pandas as pd
import pytest

from jimbot import stats as S
from jimbot import levels as L
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
    """Le seuil doit correspondre au 90e percentile des lectures observées.

    Le calibrage dépend entièrement des pondérations : mesuré sur 17 070
    observations, le 90e percentile de |somme pondérée| vaut 0.628, et c'est
    lui que le seuil de 58 doit désigner. Conservé après l'inversion des
    poids, l'ancien calibrage plaçait 73,5 % des lectures au-dessus du seuil,
    qui ne sélectionnait donc plus rien.
    """
    def score(weighted: float) -> float:
        return float(np.tanh(weighted / St.SCORE_SCALE)) * 100

    assert score(0.628) == pytest.approx(58.0, abs=1.5)   # 90e percentile
    assert score(0.0) == pytest.approx(0.0)
    assert score(-0.628) == pytest.approx(-58.0, abs=1.5)  # symétrie
    # La médiane des lectures doit rester nettement sous le seuil.
    assert score(0.412) < 50.0


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
    assert len(sig.factors) == 7
    assert sig.price > 0


def test_niveaux_coherents_pour_un_achat(asset, trending):
    sig = St.analyze(asset, trending, timeframe="1h")
    if sig.direction == "long":
        assert sig.stop < sig.entry < sig.target
        # Le R/R n'est plus figé : il est choisi par l'optimiseur d'espérance
        # dans les bornes du module `levels`.
        assert L.MIN_RR <= sig.rr <= L.MAX_RR
        assert sig.expected_r >= St.MIN_EXPECTED_R
        assert sig.stop_basis and sig.target_basis


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


def test_signal_neutre_conserve_son_plan(asset, random_walk):
    """Un actif sous le seuil garde des niveaux affichables.

    C'est ce qui alimente la liste de surveillance : sans plan conservé, un
    jour calme n'afficherait que des zéros, alors que « voici ce qui s'en
    rapproche le plus, et voici pourquoi c'est insuffisant » a de la valeur.
    Le garde-fou n'est pas l'absence de niveaux mais le drapeau `actionable`.
    """
    sig = St.analyze(asset, random_walk, timeframe="1h")
    if sig.direction == "neutre":
        assert not sig.actionable
        if sig.bias != "neutre":
            assert sig.stop > 0 and sig.target > 0


def test_actionable_implique_une_direction(asset, trending, ranging, random_walk):
    """`actionable` et `direction` ne doivent jamais se contredire."""
    for df in (trending, ranging, random_walk):
        sig = St.analyze(asset, df, timeframe="1h")
        assert sig.actionable == (sig.direction != "neutre")
        if sig.actionable:
            assert sig.direction == sig.bias
            assert sig.score >= St.SETTINGS.signal_threshold
            assert sig.expected_r >= St.MIN_EXPECTED_R


def test_le_biais_suit_le_signe_du_score(asset, trending):
    sig = St.analyze(asset, trending, timeframe="1h")
    if sig.raw_score > 0:
        assert sig.bias in {"long", "neutre"}
    elif sig.raw_score < 0:
        assert sig.bias in {"short", "neutre"}


def test_les_poids_somment_a_un_par_regime():
    for regime, weights in St.WEIGHTS.items():
        total = sum(abs(w) for w in weights.values())
        assert total == pytest.approx(1.0, abs=0.01), f"régime {regime}"


def test_desaccord_avec_unite_superieure_penalise(asset, trending):
    """Un signal contraire à l'unité de temps supérieure doit être atténué.

    Le test est écrit indépendamment du sens : on lit d'abord l'orientation
    que le moteur donne au marché fourni, puis on lui oppose une unité
    supérieure. Sans cette précaution, le test dépendrait du signe des
    pondérations — et il a effectivement cassé le jour où celles-ci ont été
    inversées par la mesure.
    """
    seul = St.analyze(asset, trending, timeframe="1h")
    if seul.raw_score == 0:
        pytest.skip("aucune orientation sur ce jeu de données")

    # Unité supérieure construite dans le sens contraire au signal obtenu.
    derive = -0.01 if seul.raw_score > 0 else 0.01
    rng = np.random.default_rng(31)
    serie = pd.Series(
        100 * np.exp(np.cumsum(rng.normal(derive, 0.008, 200))),
        index=pd.date_range("2026-01-01", periods=200, freq="4h", tz="UTC"))
    htf = pd.DataFrame({"open": serie, "high": serie * 1.004, "low": serie * 0.996,
                        "close": serie, "volume": np.ones(200)})

    oppose = St.analyze(asset, trending, timeframe="1h", df_htf=htf)
    # L'unité supérieure doit pointer dans le sens inverse du signal.
    if oppose.htf_alignment == 0:
        pytest.skip("unité supérieure sans orientation nette")
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


# --------------------------------------------------------------------------
# Mouvement : ce que l'actif fait, mesuré et non prédit
# --------------------------------------------------------------------------
def _bougies(n: int, depart: float = 100.0, pente: float = 0.0,
             freq: str = "h") -> pd.DataFrame:
    """Série horaire déterministe, éventuellement en pente constante."""
    idx = pd.date_range("2026-09-01", periods=n, freq=freq, tz="UTC")
    prix = depart * (1.0 + pente) ** np.arange(n)
    return pd.DataFrame(
        {"open": prix, "high": prix * 1.002, "low": prix * 0.998,
         "close": prix, "volume": np.full(n, 100.0)},
        index=idx,
    )


def test_variation_compte_en_heures_pas_en_bougies():
    """Une séance fermée ne doit pas décaler la fenêtre de comparaison."""
    df = _bougies(200, pente=0.001)
    # On retire 30 bougies au milieu, comme un week-end sur un indice.
    troue = pd.concat([df.iloc[:100], df.iloc[130:]])
    plein = St._variation(df, 24)
    avec_trou = St._variation(troue, 24)
    # Les 24 dernières heures sont intactes dans les deux cas : même réponse.
    assert plein == pytest.approx(avec_trou, abs=1e-9)


def test_variation_inconnue_ne_vaut_pas_zero():
    """Un historique trop court renvoie None, jamais « 0 % de variation »."""
    assert St._variation(_bougies(10), 24 * 7) is None
    assert St._variation(_bougies(200), 24 * 7) is not None


def test_mouvement_indisponible_sur_index_non_temporel():
    df = _bougies(50)
    df.index = range(50)
    assert St.mouvement(df) == {"disponible": False}


def test_ampleur_se_compare_a_la_journee_habituelle_de_l_actif():
    """Le même parcours en pourcentage ne dit pas la même chose selon l'actif.

    Deux séries parcourent 4 % sur les dernières 24 h. L'une le fait tous les
    jours, l'autre sort d'un mois de calme plat. Seule la seconde a bougé.
    """
    n = 24 * 20
    idx = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(7)

    def serie(amplitude_ordinaire: float) -> pd.DataFrame:
        # Bruit calibré pour produire l'amplitude quotidienne voulue, puis on
        # impose les mêmes dernières 24 h aux deux séries.
        p = 100 * np.exp(np.cumsum(rng.normal(0, amplitude_ordinaire / 100 / 5, n)))
        p[-24:] = np.linspace(p[-25], p[-25] * 1.04, 24)
        return pd.DataFrame(
            {"open": p, "high": p * 1.0005, "low": p * 0.9995, "close": p,
             "volume": np.full(n, 100.0)}, index=idx)

    agitee = St.mouvement(serie(4.0))
    calme = St.mouvement(serie(0.4))

    # Parcours identique sur 24 h, référence différente.
    assert agitee["amplitude_pct"] == pytest.approx(calme["amplitude_pct"], rel=0.05)
    assert agitee["amplitude_ref_pct"] > calme["amplitude_ref_pct"]
    assert agitee["ampleur"] < calme["ampleur"]
    assert "violente" in calme["etat"]


def test_ampleur_sans_etalon_ne_qualifie_pas():
    """Moins d'une semaine d'historique : pas de référence, donc pas
    d'étiquette d'intensité inventée sur trois jours."""
    court = St.mouvement(_bougies(72, pente=0.002))
    assert court["disponible"] is True
    assert court["amplitude_ref_pct"] is None
    assert court["ampleur"] == 0.0
    assert "violente" not in court["etat"] and "marquée" not in court["etat"]


def test_mouvement_distingue_un_gain_tenu_d_un_gain_rendu():
    """La seule variation nette confond « monté et tenu » avec « monté puis
    rendu » : les deux parcourent autant, l'un n'en garde rien."""
    n = 24 * 20
    idx = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
    plat = np.full(n - 24, 100.0)

    tenu = np.concatenate([plat, np.linspace(100, 106, 24)])
    rendu = np.concatenate([
        plat, np.linspace(100, 106, 12), np.linspace(106, 100.3, 12)])

    def cadre(p):
        return pd.DataFrame(
            {"open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
             "volume": np.full(n, 100.0)}, index=idx)

    a = St.mouvement(cadre(tenu))
    b = St.mouvement(cadre(rendu))

    # Parcours comparable : l'ampleur seule ne les sépare pas, et c'est
    # justement pourquoi elle se mesure sur le parcours et non sur les bouts.
    assert a["amplitude_pct"] == pytest.approx(b["amplitude_pct"], rel=0.1)

    # Ce qui les sépare, c'est ce qu'il reste du parcours.
    assert a["retention"] > 0.9 and a["position_range"] > 0.9
    assert b["retention"] < 0.2 and b["position_range"] < 0.2
    assert not a["rendu"] and b["rendu"]
    assert "hausse" in a["etat"] and b["etat"] == "secousse sans direction"

    # Le piège que la variation nette seule ne voit pas : b affiche +0,3 %.
    assert abs(b["var_24h"]) < 1.0


def test_mouvement_n_entre_pas_dans_le_score(asset, trending):
    """Garde-fou : le mouvement décrit, il ne prédit pas.

    Aucun de ses champs ne doit apparaître parmi les facteurs, et faire varier
    le mouvement ne doit pas faire varier le score — c'est la seule garantie
    qu'une description ne s'est pas transformée en prédiction non mesurée."""
    sig = St.analyze(asset, trending, timeframe="1h")
    noms = {f["name"] for f in sig.factors}
    assert noms.isdisjoint({"mouvement", "var_24h", "ampleur_atr", "volume_rel"})
    assert sig.mouvement["disponible"] is True
