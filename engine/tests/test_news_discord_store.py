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
