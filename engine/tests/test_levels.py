"""Structure de marché et optimisation des niveaux par espérance mathématique."""
import numpy as np
import pandas as pd
import pytest

from jimbot import levels as L


# --------------------------------------------------------------------------
# Détection de structure
# --------------------------------------------------------------------------
def test_pivots_detectes_sur_forme_connue():
    """Une dent de scie régulière doit produire des pivots aux sommets."""
    prix = np.tile([100, 102, 104, 102, 100, 98, 96, 98], 12).astype(float)
    idx = pd.date_range("2026-01-01", periods=len(prix), freq="1h", tz="UTC")
    df = pd.DataFrame({"open": prix, "high": prix + 0.5, "low": prix - 0.5,
                       "close": prix, "volume": np.ones(len(prix))}, index=idx)
    hauts, bas = L.swing_points(df)
    assert hauts and bas
    assert max(h.price for h in hauts) == pytest.approx(104.5)
    assert min(b.price for b in bas) == pytest.approx(95.5)


def test_pivot_exige_une_confirmation_a_droite():
    """Sans fenêtre droite, la dernière bougie serait toujours un pivot."""
    prix = np.arange(50, dtype=float)  # strictement croissant
    idx = pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": prix, "high": prix, "low": prix,
                       "close": prix, "volume": np.ones(50)}, index=idx)
    hauts, _ = L.swing_points(df)
    # Aucun sommet local sur une droite croissante.
    assert hauts == []


def test_clustering_fusionne_les_niveaux_proches():
    niveaux = [L.Level(100.0, "pivot_bas", 0.8, 1),
               L.Level(100.2, "pivot_bas", 0.7, 1),
               L.Level(100.1, "pivot_bas", 0.9, 1),
               L.Level(120.0, "pivot_haut", 0.6, 1)]
    fusionnes = L.cluster_levels(niveaux, tolerance=1.0)
    assert len(fusionnes) == 2
    zone = [n for n in fusionnes if n.price < 110][0]
    assert zone.touches == 3
    assert zone.kind == "congestion"
    # Trois contacts doivent produire une zone plus solide qu'un pivot isolé.
    assert zone.strength > max(n.strength for n in fusionnes if n.price > 110)


def test_clustering_liste_vide():
    assert L.cluster_levels([], 1.0) == []


# --------------------------------------------------------------------------
# Espérance mathématique — le cœur du module
# --------------------------------------------------------------------------
def test_marche_aleatoire_donne_une_esperance_nulle():
    """Résultat fondamental : sans avantage, aucun R/R n'est rentable.

    Pour une marche sans dérive, p = d_stop / (d_stop + d_objectif), et
    l'espérance vaut exactement zéro quel que soit le ratio choisi. Un
    système qui prétend créer de la valeur en réglant son R/R se trompe.
    """
    for rr in (1.0, 2.0, 3.0, 5.0, 10.0):
        p = L.win_probability(1.0, rr, score=50.0, regime_quality=0.0)
        assert L.expected_r(p, rr) == pytest.approx(0.0, abs=1e-9)


def test_l_avantage_vient_du_signal_pas_du_ratio():
    neutre = L.win_probability(1.0, 2.0, score=50.0, regime_quality=1.0)
    convaincu = L.win_probability(1.0, 2.0, score=100.0, regime_quality=1.0)
    assert convaincu > neutre
    assert L.expected_r(convaincu, 2.0) > 0


def test_avantage_decroit_avec_la_distance_de_l_objectif():
    """Sans cette décroissance, l'espérance croîtrait sans borne avec le R/R
    et l'optimiseur retiendrait toujours l'objectif le plus lointain."""
    proche = L.win_probability(1.0, 1.0, 90.0, 0.8, atr=1.0)
    lointain = L.win_probability(1.0, 8.0, 90.0, 0.8, atr=1.0)
    avantage_proche = proche - (1.0 / 2.0)
    avantage_lointain = lointain - (1.0 / 9.0)
    assert avantage_proche > avantage_lointain


def test_optimum_est_interieur():
    """L'espérance doit atteindre son maximum ailleurs qu'aux bornes.

    Un optimum au bord signale un modèle dégénéré, et les deux bords ont été
    rencontrés en pratique : sans décroissance de l'avantage, l'optimiseur
    retenait toujours le R/R maximal ; avec un horizon trop court, il s'est
    collé au R/R minimal.
    """
    atr, stop = 1.0, 2.0
    ratios = np.arange(1.0, 6.01, 0.25)
    esperances = [L.expected_r(
        L.win_probability(stop, rr * stop, 85.0, 0.8, atr), rr) for rr in ratios]
    meilleur = int(np.argmax(esperances))
    assert 0 < meilleur < len(ratios) - 1, "optimum au bord = modèle dégénéré"


def test_les_couts_reduisent_l_esperance():
    """Le modèle ignorait les frais : un stop touché coûte en réalité plus
    d'1 R, ce qui rendait toute espérance affichée trop optimiste."""
    sans = L.expected_r(0.4, 2.0, cost_r=0.0)
    avec = L.expected_r(0.4, 2.0, cost_r=0.1)
    assert avec == pytest.approx(sans - 0.1)


def test_le_cout_en_R_croit_quand_le_stop_se_resserre():
    """À frais constants, un stop deux fois plus serré coûte deux fois plus
    cher rapporté au risque — c'est ce qui rend les stops très fins perdants."""
    large = L.cost_in_r(100.0, 2.0, "crypto")
    serre = L.cost_in_r(100.0, 0.5, "crypto")
    assert serre == pytest.approx(large * 4, rel=1e-6)
    assert L.cost_in_r(100.0, 0.0, "crypto") == 0.0


def test_les_memecoins_coutent_plus_cher_que_le_forex():
    assert L.cost_in_r(1.0, 0.05, "meme") > L.cost_in_r(1.0, 0.05, "forex")


def test_stop_adosse_a_la_structure_est_favorise():
    sans = L.win_probability(2.0, 4.0, 80.0, 0.7, atr=1.0, stop_strength=0.0)
    avec = L.win_probability(2.0, 4.0, 80.0, 0.7, atr=1.0, stop_strength=1.0)
    assert avec > sans


def test_stop_dans_le_bruit_est_penalise():
    """Un stop trop serré est touché par une simple mèche."""
    serre = L.win_probability(0.4, 2.0, 80.0, 0.7, atr=1.0)
    large = L.win_probability(2.0, 10.0, 80.0, 0.7, atr=1.0)
    # À probabilité de base comparable (ratio 1/5 dans les deux cas), le stop
    # serré doit ressortir moins favorable.
    assert serre < large


def test_obstacle_penalise_l_objectif():
    degage = L.win_probability(2.0, 4.0, 80.0, 0.7, atr=1.0, obstacle=0.0)
    bloque = L.win_probability(2.0, 4.0, 80.0, 0.7, atr=1.0, obstacle=1.2)
    assert bloque < degage


def test_probabilite_reste_bornee():
    for args in [(1.0, 1.0, 100.0, 1.0), (0.01, 100.0, 100.0, 1.0),
                 (100.0, 0.01, 0.0, 0.0)]:
        p = L.win_probability(*args, atr=1.0)
        assert 0.0 <= p <= 1.0


# --------------------------------------------------------------------------
# Plan complet
# --------------------------------------------------------------------------
@pytest.fixture
def marche(trending):
    return trending


def test_plan_coherent_a_l_achat(marche):
    plan = L.optimal_plan(marche, "long", 75.0, regime_quality=0.7)
    assert plan.stop < plan.entry < plan.target
    assert L.MIN_RR <= plan.rr <= L.MAX_RR
    assert L.MIN_STOP_ATR <= plan.stop_atr <= L.MAX_STOP_ATR
    assert plan.stop_basis and plan.target_basis


def test_plan_coherent_a_la_vente(marche):
    plan = L.optimal_plan(marche, "short", 75.0, regime_quality=0.7)
    assert plan.target < plan.entry < plan.stop


def test_plan_neutre_sans_niveaux(marche):
    plan = L.optimal_plan(marche, "neutre", 40.0)
    assert plan.stop == 0.0 and plan.target == 0.0


def test_plan_sur_historique_minimal():
    """Un historique trop court doit produire un repli, pas une exception."""
    prix = np.array([100.0, 101.0, 102.0])
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": prix, "high": prix * 1.01, "low": prix * 0.99,
                       "close": prix, "volume": np.ones(3)}, index=idx)
    plan = L.optimal_plan(df, "long", 70.0)
    assert plan.entry > 0


def test_alternatives_sont_distinctes(marche):
    plan = L.optimal_plan(marche, "long", 75.0, regime_quality=0.7)
    couples = [(a["stop"], a["target"]) for a in plan.alternatives]
    assert len(couples) == len(set(couples)), "les alternatives doivent être dédupliquées"


def test_le_plan_retenu_est_le_meilleur(marche):
    plan = L.optimal_plan(marche, "long", 75.0, regime_quality=0.7)
    for alt in plan.alternatives:
        assert plan.expected_r >= alt["expected_r"]


def test_serialisation(marche):
    import json
    json.dumps(L.optimal_plan(marche, "long", 75.0).to_dict())
