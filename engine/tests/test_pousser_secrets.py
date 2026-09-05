"""Garde-fous de l'outil qui pousse `.env` vers GitHub.

Il manipule des identifiants et peut armer une exécution réelle : ses refus
comptent davantage que ses succès.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "pousser", RACINE / "scripts" / "pousser_secrets.py")
PS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PS)


SECRET = "eyJhbGciOiJSUzUxMiJ9.charge_utile_secrete.signature"


@pytest.fixture
def env(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# commentaire ignoré\n"
        f"CAPITAL_API_KEY={SECRET}\n"
        "CAPITAL_IDENTIFIER=moi@exemple.fr\n"
        "JIMBOT_BROKER=1\n"
        "JIMBOT_RISK_MULT=5\n"
        "CAPITAL_DEMO=0\n"
        "VIDE=\n"
        "INCONNUE=quelquechose\n", encoding="utf-8")
    return f


def _lancer(env, *args) -> str:
    sortie = io.StringIO()
    argv = sys.argv
    sys.argv = ["pousser_secrets.py", "--env", str(env), "--dry-run", *args]
    try:
        with redirect_stdout(sortie):
            PS.main()
    finally:
        sys.argv = argv
    return sortie.getvalue()


def test_aucune_valeur_n_est_jamais_affichee(env):
    """Un script d'installation qui journalise le secret qu'il installe annule
    ce qu'il sert à protéger."""
    texte = _lancer(env)
    assert SECRET not in texte
    assert "moi@exemple.fr" not in texte
    # La longueur suffit à repérer une valeur tronquée sans la révéler.
    assert f"({len(SECRET)} caractères)" in texte


def test_les_reglages_qui_engagent_de_l_argent_sont_retenus(env):
    """Armer une exécution réelle en poussant un fichier de configuration
    serait la pire façon de le faire."""
    texte = _lancer(env)
    for cle in ("JIMBOT_BROKER", "CAPITAL_DEMO"):
        assert f"RETENU    variable  {cle}" in texte or f"RETENU" in texte
    assert "JIMBOT_BROKER_ALLOW_LIVE" not in texte or "RETENU" in texte
    # Le réglage inoffensif passe.
    assert "à pousser variable  JIMBOT_RISK_MULT" in texte


def test_l_armement_reste_possible_mais_explicite(env):
    texte = _lancer(env, "--armer-le-reel")
    assert "ARME LE RÉEL" in texte
    assert "RETENU" not in texte


def test_une_cle_inconnue_est_ignoree_pas_poussee(env):
    """Pousser une clé qu'on ne sait pas classer risquerait d'exposer en
    variable, donc en clair, quelque chose qui est un secret."""
    texte = _lancer(env)
    assert "ignoré" in texte and "INCONNUE" in texte
    assert "à pousser variable  INCONNUE" not in texte


def test_une_valeur_vide_n_ecrase_rien(env):
    assert "VIDE" not in PS.lire_env(env)


def test_les_deux_familles_ne_se_recouvrent_pas():
    """Une clé à la fois secret et variable partirait en clair."""
    assert not (PS.SECRETS & PS.VARIABLES)
    assert PS.ENGAGE_DE_L_ARGENT <= PS.VARIABLES


def test_le_depot_est_deduit_de_l_origine_git():
    assert PS.depot() == "benjaminboyer00-cmyk/jimbot"
