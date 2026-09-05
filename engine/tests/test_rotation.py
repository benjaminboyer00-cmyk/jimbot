"""Rotation sectorielle : ce que le classement doit et ne doit pas dire."""
from __future__ import annotations

from jimbot import rotation


def _m(var, ampleur, retention):
    return {"disponible": True, "var_24h": var, "var_7j": var * 2,
            "ampleur": ampleur, "retention": retention, "etat": "x",
            "position_range": 0.9}


def _secteur(sym, var, ampleur, retention):
    return {"symbol": sym, "klass": "secteur", "label": sym,
            "mouvement": _m(var, ampleur, retention)}


def _action(sym, var, ampleur, retention, score=55.0):
    return {"symbol": sym, "klass": "action", "label": sym, "score": score,
            "bias": "long", "mouvement": _m(var, ampleur, retention)}


def test_un_parcours_ample_sans_retention_n_est_pas_un_aimant():
    """Un secteur qui monte fort puis rend tout n'a attiré aucun capital : la
    seule ampleur le classerait pourtant en tête."""
    r = rotation.classer([
        _secteur("XLE", 3.0, 2.4, 0.85),   # monte et garde
        _secteur("XLK", 0.1, 2.5, 0.05),   # bouge autant, ne garde rien
    ])
    assert r["aimants"] == ["XLE"]
    par_sym = {s["symbol"]: s for s in r["secteurs"]}
    assert par_sym["XLK"]["aimant"] is False
    # L'ampleur seule aurait mis XLK devant.
    assert par_sym["XLK"]["ampleur"] > par_sym["XLE"]["ampleur"]


def test_un_secteur_delaisse_est_aussi_une_destination():
    """Fuir un secteur déplace de l'argent autant qu'y entrer."""
    r = rotation.classer([_secteur("XLU", -2.8, 2.0, 0.7)])
    assert r["delaisses"] == ["XLU"] and r["aimants"] == []


def test_un_titre_qui_depasse_son_secteur_perce():
    r = rotation.classer([
        _secteur("XLE", 2.0, 1.5, 0.8),
        _action("XOM", 6.0, 3.4, 0.9),     # bien au-delà de son secteur
        _action("CVX", 1.8, 1.4, 0.8),     # suit son secteur
    ])
    # CVX n'est pas dans la table de rattachement : seul XOM est classé.
    xle = r["secteurs"][0]
    assert xle["percent"] == ["XOM"]


def test_une_maree_n_est_pas_une_rotation():
    """Tous les secteurs qui montent ensemble, ce n'est pas une rotation.

    La dispersion le dit : proche de zéro, les secteurs bougent tous pareil et
    le classement n'apprend rien.
    """
    plate = rotation.classer([_secteur(f"XL{c}", 2.0, 1.5, 0.8) for c in "EKUFV"])
    assert plate["dispersion"] == 0.0

    contrastee = rotation.classer([
        _secteur("XLE", 3.0, 2.6, 0.8), _secteur("XLU", 0.1, 0.3, 0.5)])
    assert contrastee["dispersion"] > 2.0


def test_un_secteur_sans_mouvement_est_ignore_pas_classe_a_zero():
    """Un actif dont l'historique ne permet pas de mesurer le mouvement ne doit
    pas se retrouver au milieu du classement avec une ampleur nulle."""
    r = rotation.classer([
        _secteur("XLE", 2.0, 1.5, 0.8),
        {"symbol": "XLRE", "klass": "secteur", "label": "Immobilier",
         "mouvement": {"disponible": False}},
    ])
    assert [s["symbol"] for s in r["secteurs"]] == ["XLE"]


def test_les_cryptos_et_devises_ne_polluent_pas_le_classement():
    r = rotation.classer([
        _secteur("XLE", 2.0, 1.5, 0.8),
        {"symbol": "BTC-USD", "klass": "crypto", "label": "Bitcoin",
         "mouvement": _m(5.0, 3.0, 0.9)},
    ])
    assert [s["symbol"] for s in r["secteurs"]] == ["XLE"]
