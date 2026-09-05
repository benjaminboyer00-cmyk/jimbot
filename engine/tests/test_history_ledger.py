"""Mémoire et redevabilité.

Deux fichiers auxquels le site accorde une confiance particulière : l'un
prétend dire où était un actif à une date donnée, l'autre ce qu'un signal
publié a rapporté. Les tests portent donc surtout sur ce qui les rendrait
menteurs — un doublon, un point réordonné, un verdict recalculé.
"""
import json

import numpy as np
import pandas as pd
import pytest

from jimbot import history as H
from jimbot import ledger as L


def _signal(symbol="BTC-USD", score=60.0, direction="long", price=100.0,
            regime="range", raw=1.0):
    return {"symbol": symbol, "label": symbol, "klass": "crypto",
            "direction": direction, "score": score, "raw_score": raw,
            "price": price, "regime": {"name": regime}}


# --------------------------------------------------------------------------
# Historique
# --------------------------------------------------------------------------
def test_le_score_porte_son_sens():
    """Un neutre orienté à la baisse doit ressortir négatif, pas positif."""
    assert H.score_signe(_signal(direction="long", score=60)) == 60
    assert H.score_signe(_signal(direction="short", score=60)) == -60
    assert H.score_signe(_signal(direction="neutre", score=40, raw=-2)) == -40
    assert H.score_signe(_signal(direction="neutre", score=40, raw=2)) == 40


def test_un_meme_horodatage_ne_cree_pas_de_doublon():
    """Rejouer un scan doit remplacer le point, pas l'empiler."""
    hist = H._vide()
    H.fusionner(hist, [_signal(price=100)], "2026-01-01T00:00:00+00:00")
    H.fusionner(hist, [_signal(price=111)], "2026-01-01T00:00:00+00:00")
    points = hist["actifs"]["BTC-USD"]["points"]
    assert len(points) == 1
    assert points[0][1] == 111


def test_les_points_sont_ranges_dans_le_sens_du_temps():
    hist = H._vide()
    for t in ("2026-01-03", "2026-01-01", "2026-01-02"):
        H.fusionner(hist, [_signal()], f"{t}T00:00:00+00:00")
    H.ordonner(hist)
    dates = [p[0] for p in hist["actifs"]["BTC-USD"]["points"]]
    assert dates == sorted(dates)


def test_la_retention_coupe_les_plus_anciens():
    hist = H._vide()
    horodatages = [t.isoformat() for t in pd.date_range(
        "2026-01-01", periods=H.MAX_POINTS + 25, freq="15min", tz="UTC")]
    for t in horodatages:
        H.fusionner(hist, [_signal()], t)
    H.ordonner(hist)
    points = hist["actifs"]["BTC-USD"]["points"]
    assert len(points) == H.MAX_POINTS
    # Ce sont bien les plus récents qui restent.
    assert points[-1][0] == horodatages[-1]
    assert points[0][0] == horodatages[25]


def test_un_regime_inconnu_est_ajoute_a_la_legende_sans_reordonner():
    """L'ordre de la légende est un contrat : les points stockent un indice."""
    hist = H._vide()
    avant = list(hist["regimes"])
    H.fusionner(hist, [_signal(regime="inedit")], "2026-01-01T00:00:00+00:00")
    assert hist["regimes"][: len(avant)] == avant
    assert hist["regimes"][-1] == "inedit"
    assert hist["actifs"]["BTC-USD"]["points"][0][3] == len(avant)


def test_la_serialisation_produit_un_json_valide_et_une_ligne_par_point():
    hist = H._vide()
    for i in range(3):
        H.fusionner(hist, [_signal(), _signal(symbol="ETH-USD")],
                    f"2026-01-0{i + 1}T00:00:00+00:00")
    texte = H.serialiser(H.ordonner(hist))
    relu = json.loads(texte)
    assert relu["actifs"]["BTC-USD"]["points"] == hist["actifs"]["BTC-USD"]["points"]
    # Un point tient sur une ligne : c'est ce qui rend le diff git lisible.
    assert texte.count('["2026-01-01T00:00:00+00:00"') == 2


# --------------------------------------------------------------------------
# Regroupement des émissions
# --------------------------------------------------------------------------
def _emission(t, symbol="XAUUSD", direction="long", score=60.0, entry=100.0):
    return {"symbol": symbol, "label": symbol, "klass": "forex",
            "direction": direction, "score": score, "price": entry,
            "entry": entry, "stop": entry * 0.98, "target": entry * 1.04,
            "rr": 2.0, "regime": "range", "generated_at": t}


def test_les_reemissions_rapprochees_forment_un_seul_signal():
    """Le moteur réémet à chaque scan : les compter une par une gonflerait
    l'échantillon d'un facteur vingt."""
    emissions = [_emission(f"2026-01-01T0{h}:00:00+00:00") for h in range(4)]
    eps = L.episodes(emissions)
    assert len(eps) == 1
    assert eps[0]["emissions"] == 4
    assert eps[0]["premiere_emission"] == "2026-01-01T00:00:00+00:00"


def test_une_reprise_apres_le_delai_anti_spam_est_un_nouveau_signal():
    eps = L.episodes([
        _emission("2026-01-01T00:00:00+00:00"),
        _emission("2026-01-01T09:00:00+00:00"),
    ])
    assert len(eps) == 2


def test_deux_sens_opposes_ne_se_confondent_pas():
    eps = L.episodes([
        _emission("2026-01-01T00:00:00+00:00", direction="long"),
        _emission("2026-01-01T00:15:00+00:00", direction="short"),
    ])
    assert len(eps) == 2


def test_le_plan_retenu_est_celui_de_la_premiere_emission():
    """C'est le prix qu'aurait obtenu quelqu'un agissant sur l'alerte."""
    eps = L.episodes([
        _emission("2026-01-01T00:00:00+00:00", entry=100.0),
        _emission("2026-01-01T00:15:00+00:00", entry=140.0),
    ])
    assert eps[0]["entry"] == 100.0


def test_le_score_max_pilote_la_publication_discord():
    eps = L.episodes([
        _emission("2026-01-01T00:00:00+00:00", score=59.0),
        _emission("2026-01-01T00:15:00+00:00", score=71.0),
    ])
    assert eps[0]["score_max"] == 71.0
    assert eps[0]["alerte_discord"] is True


# --------------------------------------------------------------------------
# Résolution
# --------------------------------------------------------------------------
def _bougies(closes, depart="2026-01-01T00:00:00+00:00"):
    idx = pd.date_range(depart, periods=len(closes), freq="1h", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": np.ones(len(c))}, index=idx)


def test_un_objectif_atteint_donne_un_r_positif():
    ep = L.episodes([_emission("2026-01-01T00:00:00+00:00", entry=100.0)])[0]
    df = _bougies([100, 101, 102, 105], depart="2025-12-31T23:00:00+00:00")
    v = L.resoudre(ep, df, "2026-01-02T00:00:00+00:00")
    assert v["issue"] == "cible"
    assert v["r_multiple"] > 1.5


def test_un_stop_touche_donne_environ_moins_un_r():
    ep = L.episodes([_emission("2026-01-01T00:00:00+00:00", entry=100.0)])[0]
    df = _bougies([100, 99, 97], depart="2025-12-31T23:00:00+00:00")
    v = L.resoudre(ep, df, "2026-01-02T00:00:00+00:00")
    assert v["issue"] == "stop"
    assert v["r_multiple"] == pytest.approx(-1.0, abs=0.05)


def test_un_signal_anterieur_aux_bougies_est_declare_hors_portee():
    """La fenêtre de bougies est glissante : reconstituer le début du trade
    serait une invention, pas une mesure."""
    ep = L.episodes([_emission("2026-01-01T00:00:00+00:00")])[0]
    df = _bougies([100, 101], depart="2026-02-01T00:00:00+00:00")
    assert L.resoudre(ep, df, "2026-02-02T00:00:00+00:00")["issue"] == "hors_portee"


def test_un_trade_non_tranche_reste_en_cours():
    ep = L.episodes([_emission("2026-01-01T00:00:00+00:00", entry=100.0)])[0]
    df = _bougies([100, 100.5, 100.2], depart="2025-12-31T23:00:00+00:00")
    v = L.resoudre(ep, df, "2026-01-02T00:00:00+00:00")
    assert v["issue"] == "en_cours"
    assert v["r_multiple"] is None
    assert v["r_courant"] is not None


def test_les_couts_rendent_le_gain_inferieur_au_rr_affiche():
    ep = L.episodes([_emission("2026-01-01T00:00:00+00:00", entry=100.0)])[0]
    df = _bougies([100, 100, 106], depart="2025-12-31T23:00:00+00:00")
    v = L.resoudre(ep, df, "2026-01-02T00:00:00+00:00")
    assert v["issue"] == "cible"
    assert v["r_multiple"] < ep["rr"]


# --------------------------------------------------------------------------
# Bilan
# --------------------------------------------------------------------------
def test_le_bilan_ignore_ce_qui_n_est_pas_tranche():
    signaux = [
        {"issue": "cible", "r_multiple": 2.0, "emissions": 3, "alerte_discord": True},
        {"issue": "stop", "r_multiple": -1.0, "emissions": 1, "alerte_discord": False},
        {"issue": "en_cours", "r_multiple": None, "emissions": 1, "alerte_discord": False},
        {"issue": "hors_portee", "r_multiple": None, "emissions": 1, "alerte_discord": False},
    ]
    r = L.resume(signaux)
    assert r["tranches"] == 2
    assert r["win_rate"] == 50.0
    assert r["esperance_r"] == pytest.approx(0.5)
    assert r["emissions"] == 6
    assert r["publies_discord"] == 1
    assert r["significatif"] is False


def test_une_source_en_panne_ne_perd_pas_la_derniere_mesure():
    """Sans bougies, un trade en cours garde son dernier relevé plutôt que de
    retomber à « indéterminé »."""
    emissions = [_emission("2026-01-01T00:00:00+00:00")]
    ep = L.episodes(emissions)[0]
    ancien = {**ep, "issue": "en_cours", "r_courant": 0.42, "mfe": 0.5,
              "mae": -0.1, "bougies": 3, "r_multiple": None, "resolu_le": None,
              "prix_sortie": None, "dernier_prix": 101.0,
              "mesure_le": "2026-01-01T06:00:00+00:00"}

    L_read, L_write = L.read, L.write
    L.read = lambda name, default=None: {"signaux": [ancien]} if name == "suivi" else default
    L.write = lambda name, payload: None
    try:
        payload = L.enregistrer(emissions, {}, "2026-01-01T12:00:00+00:00")
    finally:
        L.read, L.write = L_read, L_write

    (garde,) = payload["signaux"]
    assert garde["issue"] == "en_cours"
    assert garde["r_courant"] == 0.42
    # La date de mesure reste celle du dernier relevé réel : elle ne prétend
    # pas avoir été rafraîchie.
    assert garde["mesure_le"] == "2026-01-01T06:00:00+00:00"


def test_une_issue_etablie_n_est_jamais_recalculee():
    emissions = [_emission("2026-01-01T00:00:00+00:00")]
    ep = L.episodes(emissions)[0]
    fige = {**ep, "issue": "cible", "r_multiple": 2.0, "mfe": 2.1, "mae": -0.2,
            "bougies": 7, "resolu_le": "2026-01-01T07:00:00+00:00",
            "prix_sortie": 104.0, "dernier_prix": 104.0, "r_courant": 2.0,
            "mesure_le": "2026-01-01T08:00:00+00:00"}
    # Des bougies qui trancheraient dans l'autre sens si on les relisait.
    df = _bougies([100, 90, 80], depart="2025-12-31T23:00:00+00:00")

    L_read, L_write = L.read, L.write
    L.read = lambda name, default=None: {"signaux": [fige]} if name == "suivi" else default
    L.write = lambda name, payload: None
    try:
        payload = L.enregistrer(emissions, {"XAUUSD": df}, "2026-01-02T00:00:00+00:00")
    finally:
        L.read, L.write = L_read, L_write

    (garde,) = payload["signaux"]
    assert garde["issue"] == "cible"
    assert garde["r_multiple"] == 2.0
