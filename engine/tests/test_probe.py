"""Sonde de pouvoir prédictif. Données synthétiques, aucun accès réseau."""
import numpy as np
import pandas as pd
import pytest

from jimbot import probe


def _rows(n: int, ic_cible: float, graine: int = 0) -> list[dict]:
    """Fabrique des observations dont un facteur porte un IC connu.

    Permet de vérifier que la mesure retrouve un signal qu'on y a placé —
    sans quoi on ne saurait pas distinguer « aucun signal » de « mesure
    cassée ».
    """
    rng = np.random.default_rng(graine)
    facteur = rng.normal(0, 1, n)
    bruit = rng.normal(0, 1, n)
    futur = ic_cible * facteur + np.sqrt(max(0.0, 1 - ic_cible ** 2)) * bruit
    return [{"symbol": "X", "klass": "crypto", "regime": "range", "quality": 0.5,
             "index": i, "trend": float(facteur[i]),
             "momentum": float(rng.normal()), "mean_reversion": float(rng.normal()),
             "volume": float(rng.normal()), "breakout": float(rng.normal()),
             "structure": float(rng.normal()),
             **{f"fwd_{h}": float(futur[i]) for h in probe.HORIZONS}}
            for i in range(n)]


def test_spearman_est_un_pearson_sur_les_rangs():
    a = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    b = pd.Series([2.0, 4.0, 8.0, 16.0, 32.0])   # monotone mais non linéaire
    assert probe._spearman(a, b) == pytest.approx(1.0)
    assert probe._spearman(a, -b) == pytest.approx(-1.0)


def test_la_mesure_retrouve_un_signal_injecte():
    ics = probe.information_coefficients(_rows(3000, 0.20))
    mesure = ics["par_facteur"]["trend"]["ic_max"]
    assert mesure == pytest.approx(0.20, abs=0.06)
    assert ics["par_facteur"]["trend"]["significatif"]


def test_un_signe_negatif_est_detecte():
    """Un facteur qui prédit à l'envers doit ressortir négatif — c'est
    exactement ce qui a été trouvé sur les facteurs de tendance."""
    ics = probe.information_coefficients(_rows(3000, -0.20))
    assert ics["par_facteur"]["trend"]["ic_max"] < -0.1


def test_le_bruit_ne_ressort_pas_comme_significatif():
    ics = probe.information_coefficients(_rows(3000, 0.0, graine=7))
    assert abs(ics["par_facteur"]["trend"]["ic_max"]) < 0.06


def test_echantillon_insuffisant_signale():
    assert "note" in probe.information_coefficients(_rows(20, 0.2))


def test_poids_derives_suivent_le_signe_mesure():
    """Un facteur d'IC négatif doit recevoir un poids négatif : le bon usage
    d'un facteur inversé est de l'inverser, pas de l'ignorer."""
    mesures = {"range": {"observations": 1000,
                         "trend": {"ic": -0.06, "t": -4.0},
                         "momentum": {"ic": +0.03, "t": +2.5},
                         "volume": {"ic": +0.001, "t": +0.1}}}
    poids = probe.derived_weights(mesures)["range"]
    assert poids["trend"] < 0
    assert poids["momentum"] > 0
    # Non significatif : écarté plutôt qu'ajusté sur du hasard.
    assert poids["volume"] == 0.0
    assert sum(abs(v) for v in poids.values()) == pytest.approx(1.0, abs=0.05)


def test_poids_uniformes_si_rien_n_est_significatif():
    mesures = {"chaotique": {"observations": 500,
                             "trend": {"ic": 0.005, "t": 0.3},
                             "momentum": {"ic": -0.004, "t": -0.2}}}
    poids = probe.derived_weights(mesures)["chaotique"]
    assert "_note" in poids


def test_les_horizons_sont_croissants():
    assert list(probe.HORIZONS) == sorted(probe.HORIZONS)
    assert probe.HORIZONS[0] > 0


def test_le_sentiment_est_exclu_de_la_sonde():
    """Le flux de presse historique n'est pas reconstituable : mesurer le
    sentiment sur le passé produirait un chiffre sans signification."""
    assert "sentiment" not in probe.FACTORS
