"""Sentiment, publication Discord et persistance. Aucun accès réseau."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from jimbot.datasources import news


# --------------------------------------------------------------------------
# Sentiment
# --------------------------------------------------------------------------
def test_liquidation_de_shorts_est_haussiere():
    """Cas réel qui a motivé la table contextuelle : le mot « liquidation »
    est baissier, mais « short liquidations » est haussier — les vendeurs à
    découvert se font sortir, ce qui pousse le prix vers le haut."""
    score, matched = news.score_text(
        "Bitcoin price hits $80K as 24-hour crypto short liquidations pass $220M")
    assert score > 0
    assert "short squeeze" in matched


def test_liquidation_de_longs_est_baissiere():
    score, _ = news.score_text("Massive long liquidations wipe out $1B as Bitcoin crashes")
    assert score < 0


def test_pas_de_double_comptage_singulier_pluriel():
    """« inflow » ne doit pas être compté en plus de « inflows »."""
    score, matched = news.score_text("Bitcoin ETF inflows hit $1.9B this week")
    assert matched.count("inflows") + matched.count("inflow") == 1
    assert score == pytest.approx(news.BULLISH["inflows"])


def test_expression_longue_prioritaire_sur_le_mot_court():
    """« rate cut » doit être consommé avant que « cut » ne puisse matcher."""
    score, matched = news.score_text("Fed signals rate cut, dovish tone lifts markets")
    assert "rate cut" in matched and score > 0


def test_un_rejet_est_baissier():
    score, _ = news.score_text("SEC rejects spot Ethereum ETF application")
    assert score < 0


def test_negation_inverse_le_sentiment():
    positif, _ = news.score_text("Ethereum ETF approved by regulators")
    negatif, _ = news.score_text("Ethereum ETF not approved by regulators")
    assert positif > 0 > negatif


def test_hack_fortement_baissier():
    score, _ = news.score_text("Exchange hacked, $200M exploit drains funds")
    assert score < -3


def test_texte_neutre_ne_score_pas():
    score, matched = news.score_text("Company publishes quarterly newsletter")
    assert score == 0 and matched == []


def test_sentiment_borne_dans_moins_un_un():
    """Une avalanche d'articles ne doit pas pouvoir dominer le score technique."""
    articles = [news.Article(
        title="Massive hack exploit fraud bankruptcy collapse", source="X", url="",
        published="2026-01-01T00:00:00+00:00", age_hours=1.0, sentiment=-15.0,
        matched=["hack"], assets=["BTC-USD"]) for _ in range(50)]
    agg = news.sentiment_by_asset(articles)
    assert -1.0 <= agg["BTC-USD"]["score"] <= 1.0


def test_confiance_croit_avec_le_nombre_d_articles():
    """Un score sur une seule dépêche ne vaut pas un score confirmé dix fois."""
    def agg(n):
        arts = [news.Article(title="Bitcoin rally surge", source="X", url="",
                             published="2026-01-01T00:00:00+00:00", age_hours=1.0,
                             sentiment=3.0, matched=["rally"], assets=["BTC-USD"])
                for _ in range(n)]
        return news.sentiment_by_asset(arts)["BTC-USD"]["score"]
    assert agg(1) < agg(5) < agg(30)


def test_decroissance_temporelle():
    """Une nouvelle de 36 h pèse beaucoup moins qu'une nouvelle d'une heure."""
    def agg(age):
        arts = [news.Article(title="Bitcoin rally", source="X", url="",
                             published="2026-01-01T00:00:00+00:00", age_hours=age,
                             sentiment=3.0, matched=["rally"], assets=["BTC-USD"])]
        return news.sentiment_by_asset(arts)["BTC-USD"]["score"]
    assert agg(1.0) > agg(36.0)


# --------------------------------------------------------------------------
# Découpage Discord
# --------------------------------------------------------------------------
def test_decoupage_respecte_la_limite():
    from jimbot import discord
    texte = "\n\n".join(f"Paragraphe {i} : " + "mot " * 220 for i in range(8))
    chunks = discord._split(texte, discord.MAX_EMBED_DESC)
    assert all(len(c) <= discord.MAX_EMBED_DESC for c in chunks)
    assert len(chunks) > 1


def test_decoupage_d_un_paragraphe_monolithique():
    """Un seul paragraphe plus long que la limite doit être coupé par phrases."""
    from jimbot import discord
    texte = "Une phrase courte. " * 700
    chunks = discord._split(texte, discord.MAX_EMBED_DESC)
    assert all(len(c) <= discord.MAX_EMBED_DESC for c in chunks)


def test_texte_court_non_decoupe():
    from jimbot import discord
    assert discord._split("court", 4096) == ["court"]


def test_troncature_respecte_la_limite():
    from jimbot import discord
    assert len(discord._truncate("a" * 5000, 100)) == 100


def test_embed_de_signal_respecte_les_limites_discord():
    from jimbot import discord
    sig = {
        "symbol": "BTC-USD", "label": "Bitcoin", "direction": "long", "score": 75.0,
        "price": 78_000.0, "entry": 78_000.0, "stop": 76_000.0, "target": 82_000.0,
        "rr": 2.0, "atr_pct": 1.2, "timeframe": "1h", "news_score": 0.2,
        "news_count": 5, "generated_at": "2026-01-01T00:00:00+00:00",
        "regime": {"name": "tendance_haussière"},
        "factors": [{"name": "trend", "contribution": 0.2, "detail": "d" * 500}
                    for _ in range(6)],
        "warnings": ["w" * 2000],
    }
    embed = discord.signal_embed(sig, "n" * 6000)
    assert len(embed["description"]) <= discord.MAX_EMBED_DESC
    assert all(len(f["value"]) <= discord.MAX_FIELD_VALUE for f in embed["fields"])


# --------------------------------------------------------------------------
# Persistance
# --------------------------------------------------------------------------
@pytest.fixture
def store_temporaire(monkeypatch):
    from jimbot import store
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DATA_DIR", Path(d))
        yield store


def test_ecriture_relecture(store_temporaire):
    store_temporaire.write("essai", {"a": 1, "accent": "é"})
    assert store_temporaire.read("essai") == {"a": 1, "accent": "é"}


def test_lecture_d_un_fichier_absent(store_temporaire):
    assert store_temporaire.read("inexistant", "defaut") == "defaut"


def test_fichier_corrompu_ne_fait_pas_echouer(store_temporaire):
    (store_temporaire.DATA_DIR / "casse.json").write_text("{ceci n'est pas du json")
    assert store_temporaire.read("casse", []) == []


def test_ecriture_atomique_sans_fichier_temporaire_residuel(store_temporaire):
    store_temporaire.write("essai", {"x": 1})
    restes = list(store_temporaire.DATA_DIR.glob("*.tmp"))
    assert restes == []


def test_historique_borne(store_temporaire):
    store_temporaire.append_history("h", [{"i": i} for i in range(10)], cap=5)
    store_temporaire.append_history("h", [{"i": 99}], cap=5)
    hist = store_temporaire.read("h")
    assert len(hist) == 5
    assert hist[0]["i"] == 99, "les entrées les plus récentes doivent être en tête"


# --------------------------------------------------------------------------
# Non-régression : faux positifs du lexique
# --------------------------------------------------------------------------
@pytest.mark.parametrize("titre", [
    "Asia FX: Mixed central bank paths shape THB, KRW, PHP",
    "Banks, regulators join quantum-resistant crypto transfer pilot",
    "Bank of England holds rates steady",
    "Bankers meet in Basel for annual conference",
])
def test_central_bank_n_est_pas_une_interdiction(titre):
    """« ban » ne doit pas matcher dans « bank ».

    Avec un suffixe libre (\\w*), toute dépêche macro contenant « central
    bank » était scorée -2.4 comme une interdiction réglementaire — soit une
    large part du flux Reuters/FXStreet systématiquement mal orientée.
    """
    score, matched = news.score_text(titre)
    assert score == 0.0, f"faux positif : {matched}"


@pytest.mark.parametrize("titre", [
    "China bans crypto mining nationwide",
    "Exchange banned from operating in the EU",
])
def test_les_vraies_interdictions_restent_detectees(titre):
    score, _ = news.score_text(titre)
    assert score < 0


def test_les_flexions_courantes_sont_reconnues():
    """Le lexique doit couvrir singulier, pluriel et participe sans
    autoriser le préfixage libre qui produit les faux positifs."""
    for titre in ("massive outflow recorded", "massive outflows recorded"):
        assert news.score_text(titre)[0] < 0


# --------------------------------------------------------------------------
# Axe géopolitique
# --------------------------------------------------------------------------
@pytest.mark.parametrize("titre,attendu", [
    ("Israel launches airstrike on Iranian nuclear facility, escalation feared", 1),
    ("Russia announces mobilization, troops deployed near border", 1),
    ("New sanctions and export ban target the energy sector", 1),
    ("Ceasefire agreement reached, sanctions lifted", -1),
    ("Peace deal signed after diplomatic breakthrough", -1),
    ("Frappes russes sur Kiev, escalade militaire", 1),
    ("Cessez-le-feu signé, désescalade au Proche-Orient", -1),
    ("Company publishes quarterly newsletter", 0),
])
def test_tension_geopolitique(titre, attendu):
    """Le lexique doit fonctionner en anglais comme en français : sans les
    termes français, les flux France 24 et Le Monde seraient ignorés."""
    score, _ = news.score_geopolitics(titre)
    signe = 0 if abs(score) < 0.5 else (1 if score > 0 else -1)
    assert signe == attendu


def test_escalade_evitee_n_est_pas_une_escalade():
    reelle, _ = news.score_geopolitics("Missile attack on the capital")
    evitee, _ = news.score_geopolitics("Missile attack averted after talks")
    assert reelle > 0 > evitee


def test_risk_off_borne_et_signe():
    def articles(risque, n=4):
        return [news.Article(
            title="t", source="s", url="", published="p", age_hours=1.0,
            sentiment=0.0, matched=[], assets=[], category="monde",
            risk=risque, risk_terms=["war"]) for _ in range(n)]
    escalade = news.risk_off_level(articles(6.0))
    apaisement = news.risk_off_level(articles(-6.0))
    assert 0 < escalade["level"] <= 1.0
    assert -1.0 <= apaisement["level"] < 0


def test_risk_off_ignore_les_articles_sans_terme():
    """Diluer par les dépêches neutres rendrait un jour de crise
    indiscernable d'un jour calme."""
    neutres = [news.Article(title="t", source="s", url="", published="p",
                            age_hours=1.0, sentiment=0.0, matched=[], assets=[],
                            category="monde", risk=0.0, risk_terms=[])
               for _ in range(50)]
    tendu = news.Article(title="war", source="s", url="", published="p",
                         age_hours=1.0, sentiment=0.0, matched=[], assets=[],
                         category="monde", risk=6.0, risk_terms=["war"])
    assert news.risk_off_level(neutres + [tendu])["level"] > 0.2


def test_beta_refuge_inverse_le_signe_selon_l_actif():
    """Une escalade doit être haussière pour l'or et baissière pour le Nasdaq."""
    arts = [news.Article(title="war escalation", source="s", url="", published="p",
                         age_hours=1.0, sentiment=0.0, matched=[], assets=[],
                         category="monde", risk=6.0, risk_terms=["war"])
            for _ in range(5)]
    agg = news.sentiment_by_asset(arts, ["XAUUSD", "NDX", "VIX", "BTC-USD"])
    assert agg["XAUUSD"]["geo"] > 0
    assert agg["VIX"]["geo"] > 0
    assert agg["NDX"]["geo"] < 0
    assert agg["BTC-USD"]["geo"] < 0


def test_tous_les_actifs_demandes_sont_couverts():
    """L'axe géopolitique ne cite aucun actif : sans la liste explicite, il
    n'atteindrait presque personne."""
    arts = [news.Article(title="war", source="s", url="", published="p",
                         age_hours=1.0, sentiment=0.0, matched=[], assets=[],
                         category="monde", risk=5.0, risk_terms=["war"])]
    agg = news.sentiment_by_asset(arts, ["XAUUSD", "SPX", "BTC-USD"])
    assert set(agg) >= {"XAUUSD", "SPX", "BTC-USD"}


# --------------------------------------------------------------------------
# Discours de politique monétaire
# --------------------------------------------------------------------------
def test_discours_restrictif_detecte():
    r = news.score_speech("Powell says rates will stay higher for longer amid sticky inflation")
    assert r["is_speech"] and r["speaker"] == "powell"
    assert r["tone"] < 0 and r["importance"] > 0.5


def test_discours_accommodant_detecte():
    r = news.score_speech("Fed Chair Powell signals rate cut, dovish remarks")
    assert r["tone"] > 0 and r["importance"] > 0.5


def test_simple_mention_n_est_pas_un_discours():
    """Citer la Fed dans un éditorial ne constitue pas une prise de parole."""
    r = news.score_speech("Analyst thinks the Fed is wrong about inflation")
    assert r["importance"] == 0.0


def test_orateur_sans_tonalite_reste_sans_importance():
    r = news.score_speech("Powell attends the annual banking conference")
    assert r["speaker"] == "powell"
    assert r["importance"] == 0.0, "c'est le propos qui compte, pas le nom"


def test_or_est_le_plus_sensible_a_la_politique_monetaire():
    assert news.MONETARY_BETA["XAUUSD"] == max(news.MONETARY_BETA.values())


def test_dollar_reagit_a_l_inverse_de_l_or():
    assert news.MONETARY_BETA["DXY"] < 0 < news.MONETARY_BETA["XAUUSD"]


def test_alerte_discours_au_dela_du_seuil():
    arts = [news.Article(
        title="Powell signals rate cut, dovish tone", source="Fed", url="",
        published="p", age_hours=1.0, sentiment=0.0, matched=[], assets=[],
        category="monde", risk=0.0, risk_terms=[],
        speech=news.score_speech("Powell signals rate cut, dovish tone"))]
    majeurs = news.major_speeches(arts)
    assert majeurs and majeurs[0]["speaker"] == "powell"
    # L'effet attendu est calculé, jamais rédigé.
    assert majeurs[0]["impact"]["XAUUSD"] > 0


# --------------------------------------------------------------------------
# Lecture de la configuration
# --------------------------------------------------------------------------
def test_variable_vide_traitee_comme_absente(monkeypatch):
    """GitHub Actions exporte `FOO: ${{ vars.FOO }}` comme chaîne vide quand
    la variable de dépôt n'existe pas. `os.getenv(nom, defaut)` renvoie alors
    "" et non le défaut, et `float("")` faisait échouer toutes les exécutions
    planifiées."""
    from jimbot import config
    monkeypatch.setenv("JIMBOT_TEST_SEUIL", "")
    assert config._env_float("JIMBOT_TEST_SEUIL", 58.0) == 58.0
    assert config._env("JIMBOT_TEST_SEUIL", "defaut") == "defaut"


def test_variable_absente_utilise_le_defaut(monkeypatch):
    from jimbot import config
    monkeypatch.delenv("JIMBOT_TEST_ABSENT", raising=False)
    assert config._env_float("JIMBOT_TEST_ABSENT", 12.5) == 12.5


def test_variable_definie_est_lue(monkeypatch):
    from jimbot import config
    monkeypatch.setenv("JIMBOT_TEST_SEUIL", "72.5")
    assert config._env_float("JIMBOT_TEST_SEUIL", 58.0) == 72.5


def test_valeur_illisible_ne_fait_pas_echouer(monkeypatch):
    """Une saisie erronée ne doit pas interrompre un scan."""
    from jimbot import config
    monkeypatch.setenv("JIMBOT_TEST_SEUIL", "soixante")
    assert config._env_float("JIMBOT_TEST_SEUIL", 58.0) == 58.0


# --------------------------------------------------------------------------
# Anti-spam : une simulation ne doit rien consommer
# --------------------------------------------------------------------------
def test_le_mode_simulation_ne_consomme_pas_le_delai(monkeypatch, store_temporaire):
    """Un essai en --dry-run ne doit pas bloquer une alerte réelle.

    Le cas s'est produit : un test local a marqué l'alerte géopolitique comme
    envoyée, l'état a été committé, et l'exécution suivante en production l'a
    respecté — l'alerte n'est jamais partie.
    """
    import dataclasses
    from jimbot import discord

    monkeypatch.setattr(discord, "read", store_temporaire.read)
    monkeypatch.setattr(discord, "write", store_temporaire.write)
    # `Settings` est un dataclass gelé : on substitue une copie modifiée.
    monkeypatch.setattr(discord, "SETTINGS",
                        dataclasses.replace(discord.SETTINGS, dry_run=True))

    discord.mark_context_alerted("geo:escalade:0.7")
    discord.mark_alerted("XAUUSD", "long")

    assert store_temporaire.read("context_sent", {}) is None or \
           store_temporaire.read("context_sent", {}) == {}
    assert discord.should_alert_context("geo:escalade:0.7")
    assert discord.should_alert("XAUUSD", "long")


def test_un_envoi_reel_consomme_bien_le_delai(monkeypatch, store_temporaire):
    import dataclasses
    from jimbot import discord

    monkeypatch.setattr(discord, "read", store_temporaire.read)
    monkeypatch.setattr(discord, "write", store_temporaire.write)
    monkeypatch.setattr(discord, "SETTINGS",
                        dataclasses.replace(discord.SETTINGS, dry_run=False))

    discord.mark_context_alerted("geo:escalade:0.7")
    assert not discord.should_alert_context("geo:escalade:0.7")
