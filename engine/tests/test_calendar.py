"""Échéances à venir. Aucun accès réseau, aucune date inscrite en dur."""
from datetime import date

import pytest

from jimbot import calendar as C


# --------------------------------------------------------------------------
# Règles de calendrier
# --------------------------------------------------------------------------
@pytest.mark.parametrize("year,month", [(2026, m) for m in range(1, 13)])
def test_le_nfp_tombe_toujours_un_premier_vendredi(year, month):
    """La règle doit être exacte par construction, sur tous les mois."""
    d = C._nth_weekday(year, month, 4, 1)
    assert d.weekday() == 4, "doit être un vendredi"
    assert d.day <= 7, "doit être le premier du mois"


@pytest.mark.parametrize("year,month", [(2026, m) for m in range(1, 13)])
def test_l_expiration_tombe_un_troisieme_vendredi(year, month):
    d = C._nth_weekday(year, month, 4, 3)
    assert d.weekday() == 4
    assert 15 <= d.day <= 21


def test_dernier_jour_ouvre_n_est_jamais_un_week_end():
    for month in range(1, 13):
        d = C._last_business_day(2026, month)
        assert d.weekday() < 5
        assert d.month == month


def test_dernier_jour_ouvre_de_decembre():
    """Cas limite : le passage à l'année suivante."""
    d = C._last_business_day(2026, 12)
    assert d.month == 12 and d.weekday() < 5


def test_les_echeances_restent_dans_l_horizon():
    today = date(2026, 8, 25)
    for e in C.mechanical_events(today, horizon_days=21):
        assert 0 <= e.days_ahead <= 21
        assert e.kind == "mecanique"
        assert e.date >= today.isoformat()


def test_le_nfp_est_signale_comme_impact_eleve():
    events = C.mechanical_events(date(2026, 8, 25), horizon_days=21)
    nfp = [e for e in events if "NFP" in e.label]
    assert nfp and nfp[0].impact == "eleve"


def test_les_fins_de_trimestre_pesent_plus_que_les_fins_de_mois():
    trimestre = C.mechanical_events(date(2026, 9, 20), horizon_days=21)
    mois = C.mechanical_events(date(2026, 8, 20), horizon_days=21)
    fin_t = next(e for e in trimestre if "trimestre" in e.label.lower())
    fin_m = next(e for e in mois if e.label == "Fin de mois")
    assert fin_t.impact == "moyen" and fin_m.impact == "faible"


# --------------------------------------------------------------------------
# Extraction depuis la presse
# --------------------------------------------------------------------------
def _art(titre: str, source: str = "X") -> dict:
    return {"title": titre, "source": source, "url": ""}


@pytest.mark.parametrize("titre,attendu", [
    ("Gold steady ahead of Thursday CPI release", True),
    ("Traders await next week Fed meeting for rate decision", True),
    ("ECB set to decide on rates as inflation cools", True),
    ("OPEC meeting scheduled for next month", True),
    ("La prochaine réunion de la BCE la semaine prochaine", True),
    # Rétrospectifs : le fait est survenu, ce n'est pas une échéance.
    ("US CPI rose 0.2% in July, data showed", False),
    ("Fed cut rates yesterday", False),
    ("Company publishes quarterly newsletter", False),
])
def test_seuls_les_titres_prospectifs_sont_retenus(titre, attendu):
    trouve = bool(C.press_events([_art(titre)]))
    assert trouve == attendu


def test_aucune_date_n_est_inventee():
    """Le module ne connaît pas la date de l'événement annoncé : seule la
    source la connaît, et elle reste dans le titre cité."""
    events = C.press_events([_art("Gold steady ahead of Thursday CPI release", "Reuters")])
    assert events
    e = events[0]
    assert e.days_ahead == 0, "aucune échéance ne doit être supposée"
    assert e.source == "Reuters"
    assert "CPI" in e.detail, "le titre d'origine doit rester vérifiable"


def test_pas_de_doublon_de_sujet():
    """Cinq dépêches sur la même réunion ne font pas cinq échéances."""
    arts = [_art(f"Traders await next week Fed meeting, take {i}") for i in range(5)]
    labels = [e.label for e in C.press_events(arts)]
    assert len(labels) == len(set(labels))


def test_classement_par_impact():
    arts = [_art("OPEC meeting scheduled for next month"),
            _art("Gold steady ahead of Thursday CPI release")]
    events = C.press_events(arts)
    assert events[0].impact == "eleve", "l'impact élevé doit passer devant"


def test_agenda_complet_serialisable():
    import json
    agenda = C.upcoming([_art("Traders await next week Fed meeting")], date(2026, 8, 25))
    json.dumps(agenda)
    assert "mechanical" in agenda and "press" in agenda
    assert agenda["high_impact"] >= 1
