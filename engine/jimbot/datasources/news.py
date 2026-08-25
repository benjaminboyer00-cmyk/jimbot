"""Actualités via flux RSS publics + scoring de sentiment déterministe.

Le sentiment est calculé par lexique pondéré, pas par LLM : c'est
reproductible, gratuit, instantané, et surtout auditable — on peut toujours
dire quels mots ont produit quel score. Le LLM n'intervient qu'en aval, pour
rédiger, jamais pour chiffrer.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

import feedparser
import numpy as np

log = logging.getLogger("jimbot.data.news")

# Flux RSS, avec leur catégorie. Les flux « marchés » parlent d'actifs
# nommés ; les flux « monde » parlent d'événements qui déplacent les marchés
# sans jamais citer un actif — ils alimentent l'axe risque-on/risque-off.
FEEDS: list[tuple[str, str, str]] = [
    # --- Marchés & crypto ---
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "marches"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "marches"),
    ("Decrypt", "https://decrypt.co/feed", "marches"),
    ("TheBlock", "https://www.theblock.co/rss.xml", "marches"),
    ("FXStreet", "https://www.fxstreet.com/rss/news", "marches"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "marches"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "marches"),
    ("NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "marches"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "marches"),
    # --- Monde & géopolitique ---
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "monde"),
    ("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "monde"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "monde"),
    ("Guardian World", "https://www.theguardian.com/world/rss", "monde"),
    ("AP News", "https://feedx.net/rss/ap.xml", "monde"),
    ("France 24", "https://www.france24.com/fr/rss", "monde"),
    ("Le Monde", "https://www.lemonde.fr/international/rss_full.xml", "monde"),
    # --- Banques centrales (communiqués officiels) ---
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml", "monde"),
    ("BCE", "https://www.ecb.europa.eu/rss/press.html", "monde"),
]

# Lexique pondéré. Les poids reflètent l'ampleur historique de la réaction
# du marché, pas l'intensité émotionnelle du mot.
BULLISH = {
    "etf approval": 3.0, "approved": 2.0, "adoption": 1.8, "partnership": 1.5,
    "institutional": 1.8, "inflow": 2.0, "inflows": 2.0, "accumulation": 1.5,
    "rally": 1.5, "surge": 1.5, "soar": 1.6, "breakout": 1.4, "all-time high": 2.2,
    "record high": 2.0, "bullish": 1.5, "upgrade": 1.3, "buyback": 1.5,
    "rate cut": 2.2, "dovish": 1.8, "stimulus": 1.6, "halving": 1.4,
    "integration": 1.2, "listing": 1.4, "mainnet": 1.2,
}
BEARISH = {
    "hack": -3.0, "hacked": -3.0, "exploit": -2.8, "rug pull": -3.0, "rugpull": -3.0,
    "scam": -2.5, "fraud": -2.5, "lawsuit": -2.0, "sec charges": -2.5, "sued": -2.0,
    "ban": -2.4, "banned": -2.4, "crackdown": -2.2, "investigation": -1.8,
    "outflow": -2.0, "outflows": -2.0, "liquidation": -2.2, "liquidations": -2.2,
    "crash": -2.5, "plunge": -2.2, "plummet": -2.2, "selloff": -2.0, "sell-off": -2.0,
    "bearish": -1.5, "downgrade": -1.5, "delisting": -2.0, "bankruptcy": -3.0,
    "insolvency": -2.8, "rate hike": -2.2, "hawkish": -1.8, "recession": -2.0,
    "halt": -1.6, "freeze": -1.8, "default": -2.4, "collapse": -2.6,
    "rejects": -2.0, "rejected": -2.0, "denies": -1.6, "denied": -1.6,
    "postponed": -1.4, "delayed": -1.3, "warns": -1.4, "probe": -1.8,
}

# --------------------------------------------------------------------------
# Discours et rhétorique de politique monétaire
# --------------------------------------------------------------------------
# L'or ne réagit pas au bilan des entreprises : il réagit aux taux réels et à
# la confiance dans la monnaie. Un discours de banque centrale le déplace donc
# plus sûrement qu'un communiqué d'entreprise, et souvent avant que les
# chiffres ne sortent. On détecte l'orateur, puis la tonalité du propos.

# Orateurs, avec leur poids d'influence sur les marchés.
SPEAKERS = {
    "powell": 1.0, "fomc": 1.0, "federal reserve": 0.9, "fed chair": 1.0,
    "lagarde": 0.85, "ecb": 0.8, "bce": 0.8, "european central bank": 0.8,
    "bailey": 0.6, "bank of england": 0.6, "boe": 0.55,
    "ueda": 0.6, "bank of japan": 0.6, "boj": 0.55,
    "yellen": 0.7, "treasury secretary": 0.7, "bessent": 0.7,
    "williams": 0.5, "waller": 0.5, "bowman": 0.45, "jefferson": 0.45,
    "imf": 0.5, "world bank": 0.4, "davos": 0.4, "jackson hole": 0.9,
}

# Marqueurs indiquant qu'il s'agit bien d'une prise de parole et non d'un
# simple article citant l'institution.
SPEECH_MARKERS = [
    "speech", "remarks", "testimony", "press conference", "statement",
    "minutes", "says", "said", "warns", "signals", "comments", "testifies",
    "interview", "discours", "déclare", "déclaration", "conférence de presse",
]

# Tonalité monétaire. Positif = accommodant (baisse de taux attendue), ce qui
# est haussier pour l'or ; négatif = restrictif, baissier pour l'or.
MONETARY_TONE = {
    # Accommodant
    "rate cut": 2.4, "cut rates": 2.4, "cutting rates": 2.4, "dovish": 2.2,
    "easing": 1.8, "accommodative": 2.0, "stimulus": 1.8, "liquidity injection": 2.0,
    "quantitative easing": 2.2, "pause hikes": 1.6, "patient": 0.8,
    "downside risks": 1.4, "slowing economy": 1.4, "soft landing": 0.8,
    "disinflation": 1.6, "cooling inflation": 1.6, "baisse des taux": 2.4,
    "assouplissement": 1.8, "accommodante": 2.0,
    # Restrictif
    "rate hike": -2.4, "raise rates": -2.4, "hiking": -2.2, "hawkish": -2.2,
    "tightening": -2.0, "restrictive": -2.0, "higher for longer": -2.2,
    "quantitative tightening": -2.0, "inflation persists": -1.8,
    "sticky inflation": -1.8, "overheating": -1.6, "upside risks to inflation": -1.8,
    "hausse des taux": -2.4, "resserrement": -2.0, "restrictive policy": -2.0,
}

# Sensibilité de chaque actif à la tonalité monétaire, dans [-1, +1].
# Positif = monte quand la politique s'assouplit.
MONETARY_BETA = {
    "XAUUSD": 1.00,   # l'or est l'actif le plus directement lié aux taux réels
    "BTC-USD": 0.70,  # se comporte de plus en plus comme un actif de duration
    "ETH-USD": 0.65,
    "SOL-USD": 0.60, "AVAX-USD": 0.60, "LINK-USD": 0.55,
    "XRP-USD": 0.50, "BNB-USD": 0.50, "DOGE-USD": 0.60,
    "NDX": 0.85,      # les valeurs de croissance sont très sensibles aux taux
    "SPX": 0.65,
    "DXY": -0.75,     # le dollar s'affaiblit quand la Fed assouplit
    "EURUSD": 0.55, "GBPUSD": 0.50,
    "USDJPY": -0.45,
    "VIX": -0.20,
}
DEFAULT_MONETARY_BETA = 0.30

# Poids de l'axe monétaire dans le score de presse.
MONETARY_WEIGHT = 0.60

# Au-delà de cette importance, le discours mérite une alerte dédiée.
SPEECH_ALERT_THRESHOLD = 0.55


def score_speech(text: str) -> dict:
    """Détecte une prise de parole officielle et en mesure la tonalité.

    Renvoie `{"is_speech", "speaker", "influence", "tone", "terms",
    "importance"}`. L'importance combine l'influence de l'orateur et
    l'intensité du propos : un « higher for longer » de Powell compte, la même
    phrase dans un éditorial ne compte pas.
    """
    low = f" {text.lower()} "

    speaker, influence = None, 0.0
    for name, weight in SPEAKERS.items():
        if name in low and weight > influence:
            speaker, influence = name, weight
    if speaker is None:
        return {"is_speech": False, "speaker": None, "influence": 0.0,
                "tone": 0.0, "terms": [], "importance": 0.0}

    is_speech = any(m in low for m in SPEECH_MARKERS)

    tone, terms = 0.0, []
    remaining = low
    for term in sorted(MONETARY_TONE, key=len, reverse=True):
        pattern = rf"\b{re.escape(term)}(?:s|es|ed|ing)?\b"
        remaining, n = re.subn(pattern, " ", remaining)
        if n:
            tone += MONETARY_TONE[term]
            terms.append(term)

    # Une tonalité nulle n'a pas d'intérêt directionnel, même de la part de
    # Powell : c'est le propos qui compte, pas la seule présence du nom.
    intensity = min(1.0, abs(tone) / 4.0)
    importance = influence * intensity * (1.0 if is_speech else 0.6)

    return {"is_speech": is_speech, "speaker": speaker, "influence": influence,
            "tone": round(tone, 2), "terms": terms[:5],
            "importance": round(importance, 3)}


def monetary_stance(articles: list["Article"]) -> dict:
    """Orientation monétaire agrégée, bornée dans [-1, +1].

    Positif = accommodant (haussier pour l'or et les actifs de duration).
    Seuls les articles porteurs d'une tonalité comptent, pour la même raison
    que pour l'axe géopolitique : diluer par les dépêches neutres rendrait
    une journée de FOMC indiscernable d'une journée creuse.
    """
    contributors = [a for a in articles if a.speech and a.speech.get("terms")]
    if not contributors:
        return {"stance": 0.0, "raw": 0.0, "count": 0, "speeches": []}

    # Chaque article est pondéré par la fraîcheur ET par l'influence de
    # l'orateur : une phrase de Powell ne pèse pas comme celle d'un
    # gouverneur régional.
    weighted = [(a.speech["tone"],
                 (0.5 ** (a.age_hours / 18.0)) * max(a.speech["influence"], 0.1))
                for a in contributors]
    raw = _weighted(weighted)
    recency = max(w for _, w in weighted)
    stance = math.tanh(raw * _count_confidence(len(contributors)) * recency / 3.0)

    notables = sorted(contributors, key=lambda a: -a.speech["importance"])[:5]
    return {
        "stance": round(stance, 3),
        "raw": round(raw, 2),
        "count": len(contributors),
        "speeches": [{"title": a.title, "source": a.source, "url": a.url,
                      "speaker": a.speech["speaker"], "tone": a.speech["tone"],
                      "terms": a.speech["terms"],
                      "importance": a.speech["importance"]} for a in notables],
    }


def major_speeches(articles: list["Article"]) -> list[dict]:
    """Discours suffisamment importants pour justifier une alerte dédiée."""
    out = []
    for a in articles:
        sp = a.speech or {}
        if sp.get("importance", 0.0) >= SPEECH_ALERT_THRESHOLD:
            out.append({
                "title": a.title, "source": a.source, "url": a.url,
                "speaker": sp["speaker"], "tone": sp["tone"],
                "terms": sp["terms"], "importance": sp["importance"],
                "age_hours": a.age_hours,
                # L'effet attendu est calculé, pas rédigé : c'est la tonalité
                # multipliée par la sensibilité de l'actif aux taux.
                "impact": {sym: round(math.tanh(sp["tone"] / 4.0) * beta, 3)
                           for sym, beta in sorted(
                               MONETARY_BETA.items(),
                               key=lambda kv: -abs(kv[1]))[:6]},
            })
    return sorted(out, key=lambda x: -x["importance"])


# --------------------------------------------------------------------------
# Axe risque-on / risque-off
# --------------------------------------------------------------------------
# Une escalade géopolitique n'a pas le même signe selon l'actif : elle fait
# monter l'or et le VIX, et baisser les indices et la crypto. Un score de
# sentiment unique par actif ne peut pas représenter ça. On mesure donc
# séparément le niveau de tension mondiale, puis on l'applique à chaque actif
# avec le signe et l'intensité qui lui sont propres.
#
# Poids positif = escalade (fuite vers la qualité), négatif = apaisement.
GEOPOLITICAL = {
    # Conflit armé
    "invasion": 3.0, "airstrike": 2.8, "air strike": 2.8, "missile strike": 2.8,
    "war": 2.2, "military strike": 2.8, "troops deployed": 2.2, "offensive": 1.8,
    "escalation": 2.4, "escalate": 2.2, "retaliation": 2.4, "retaliate": 2.2,
    "attack": 2.0, "bombing": 2.6, "drone strike": 2.4, "casualties": 1.8,
    "nuclear": 2.6, "mobilisation": 2.0, "mobilization": 2.0,
    # Contrainte économique
    "sanctions": 2.2, "embargo": 2.4, "tariff": 1.8, "tariffs": 1.8,
    "trade war": 2.4, "export ban": 2.2, "blockade": 2.6, "seizure": 1.6,
    "supply disruption": 2.0, "shortage": 1.6,
    # Instabilité politique
    "coup": 2.8, "unrest": 1.8, "protests": 1.4, "riots": 2.0,
    "state of emergency": 2.4, "impeachment": 1.6, "government collapse": 2.4,
    "shutdown": 1.8, "debt ceiling": 2.0, "default risk": 2.4,
    # Apaisement
    "ceasefire": -2.4, "peace deal": -2.8, "peace agreement": -2.8,
    "truce": -2.2, "de-escalation": -2.4, "sanctions lifted": -2.2,
    "sanctions relief": -2.0, "trade deal": -1.8, "agreement reached": -1.6,
    "diplomatic breakthrough": -2.2, "talks resume": -1.4, "deal signed": -1.6,
}

# Équivalents français : les flux France 24 et Le Monde seraient sinon
# entièrement ignorés, faute de terme reconnu.
GEOPOLITICAL_FR = {
    "invasion": 3.0, "frappe": 2.6, "frappes": 2.6, "guerre": 2.2,
    "missile": 2.4, "bombardement": 2.6, "escalade": 2.4, "représailles": 2.4,
    "attaque": 2.0, "offensive": 1.8, "nucléaire": 2.6, "mobilisation": 2.0,
    "sanctions": 2.2, "embargo": 2.4, "droits de douane": 1.8,
    "guerre commerciale": 2.4, "blocus": 2.6, "pénurie": 1.6,
    "coup d'état": 2.8, "émeutes": 2.0, "manifestations": 1.2,
    "état d'urgence": 2.4, "crise politique": 1.8,
    "cessez-le-feu": -2.4, "accord de paix": -2.8, "trêve": -2.2,
    "désescalade": -2.4, "levée des sanctions": -2.2, "accord commercial": -1.8,
    "négociations": -1.0, "accord signé": -1.6,
}

# Bêta de valeur refuge, dans [-1, +1].
#  +1 : l'actif profite pleinement d'une fuite vers la qualité
#   0 : indifférent au climat géopolitique
#  -1 : actif de risque, vendu en premier lors d'une escalade
HAVEN_BETA = {
    "XAUUSD": 0.90,   # l'or est la valeur refuge de référence
    "VIX": 1.00,      # l'indice de volatilité monte mécaniquement avec le stress
    "DXY": 0.50,      # le dollar s'apprécie en période de tension
    "USDJPY": -0.40,  # c'est le yen qui est refuge : la paire baisse
    "EURUSD": -0.30,  # l'euro cède face au dollar
    "GBPUSD": -0.35,
    "SPX": -0.80,
    "NDX": -0.90,     # les valeurs de croissance souffrent le plus
    "BTC-USD": -0.55, # la crypto se comporte comme un actif de risque
    "ETH-USD": -0.60,
    "SOL-USD": -0.70,
    "BNB-USD": -0.60,
    "XRP-USD": -0.60,
    "AVAX-USD": -0.70,
    "LINK-USD": -0.70,
    "DOGE-USD": -0.85,  # les actifs les plus spéculatifs ont le bêta le plus fort
}
DEFAULT_HAVEN_BETA = -0.60  # tout actif non listé est traité comme un actif de risque

# Poids de l'axe géopolitique dans le score de presse final. Volontairement
# modeste : la géopolitique oriente le climat, elle ne remplace pas la lecture
# technique de l'actif.
GEO_WEIGHT = 0.55

# Rattachement d'un article à un actif suivi.
ASSET_KEYWORDS = {
    "BTC-USD": ["bitcoin", "btc", "satoshi"],
    "ETH-USD": ["ethereum", "ether ", "eth ", "vitalik"],
    "SOL-USD": ["solana", "sol "],
    "BNB-USD": ["binance coin", "bnb"],
    "XRP-USD": ["xrp", "ripple"],
    "DOGE-USD": ["dogecoin", "doge"],
    "AVAX-USD": ["avalanche", "avax"],
    "LINK-USD": ["chainlink", "link token"],
    "EURUSD": ["euro", "ecb", "eurozone", "european central bank"],
    "GBPUSD": ["pound", "sterling", "bank of england", "boe "],
    "USDJPY": ["yen", "bank of japan", "boj "],
    "XAUUSD": ["gold", "bullion"],
    "SPX": ["s&p 500", "s&p500", "wall street", "stocks"],
    "NDX": ["nasdaq", "tech stocks"],
    "DXY": ["dollar index", "greenback", "us dollar"],
    "VIX": ["volatility index", "vix"],
}

# Un actif est aussi touché par la macro générale, avec un poids réduit.
MACRO_KEYWORDS = ["fed ", "fomc", "inflation", "cpi", "interest rate", "powell",
                  "treasury yield", "jobs report", "gdp", "tariff"]
MACRO_WEIGHT = 0.4

# Expressions dont le sens s'inverse selon le contexte immédiat. Elles sont
# testées AVANT le lexique simple et consomment le texte correspondant, ce qui
# évite qu'un terme générique ne les recompte à contresens.
# Cas d'école : « short liquidations » est haussier (les vendeurs à découvert
# se font sortir, ça pousse le prix vers le haut), alors que le mot
# « liquidation » seul est fortement baissier.
CONTEXTUAL: dict[str, tuple[float, str]] = {
    r"short (?:squeeze|liquidation)\w*": (2.2, "short squeeze"),
    r"liquidat\w* (?:of )?shorts": (2.2, "short squeeze"),
    r"long liquidation\w*": (-2.2, "liquidation de longs"),
    r"bear(?:ish)? (?:trap|capitulation)": (1.5, "capitulation baissière"),
    r"bull trap": (-1.8, "bull trap"),
    r"sell(?:ing)? pressure eas\w+": (1.4, "pression vendeuse en baisse"),
    r"buy(?:ing)? the dip": (1.2, "achat sur repli"),
    r"profit[- ]taking": (-1.2, "prises de bénéfices"),
    r"record (?:outflow|redemption)\w*": (-2.4, "sorties record"),
    r"(?:consolidation|correction|pullback) risk": (-1.2, "risque de consolidation"),
    r"treasury (?:purchase|buy\w*|allocation|strateg\w+)": (1.6, "achat en trésorerie"),
}

# Un score agrégé sur une seule dépêche n'a pas la même valeur qu'un score
# confirmé par dix sources : la confiance croît avec le nombre d'articles et
# sature. n=1 -> 0.41, n=3 -> 0.65, n=10 -> 0.85, n=30 -> 0.94.
def _count_confidence(n: int) -> float:
    return n / (n + 1.4)


@dataclass
class Article:
    title: str
    source: str
    url: str
    published: str      # ISO 8601 UTC
    age_hours: float
    sentiment: float    # score brut de sentiment, non borné
    matched: list[str]  # termes du lexique déclenchés
    assets: list[str]   # symboles concernés
    category: str = "marches"   # "marches" | "monde"
    risk: float = 0.0           # tension géopolitique : >0 escalade, <0 apaisement
    risk_terms: list[str] = field(default_factory=list)
    speech: dict = field(default_factory=dict)  # analyse de prise de parole officielle

    def to_dict(self) -> dict:
        return asdict(self)


def score_text(text: str) -> tuple[float, list[str]]:
    """Score de sentiment d'un texte + liste des termes déclenchés.

    Chaque terme reconnu est retiré du texte avant de chercher les suivants.
    Sans cette consommation, « liquidations » déclencherait à la fois
    « liquidation » et « liquidations » et compterait double ; et une
    expression contextuelle comme « short liquidations » serait écrasée par le
    sens générique du mot « liquidation ».
    """
    low = f" {text.lower()} "
    score, matched = 0.0, []

    def consume(pattern: str, weight: float, label: str) -> None:
        nonlocal low, score
        new_low, n = re.subn(pattern, " ", low)
        if n:
            low = new_low
            score += weight * n
            matched.append(label)

    # 1) Expressions contextuelles d'abord : elles ont priorité sur le lexique.
    for pattern, (weight, label) in CONTEXTUAL.items():
        consume(pattern, weight, label)

    # 2) Lexique simple, du terme le plus long au plus court, pour que
    #    « rate cut » soit consommé avant que « cut » ne puisse matcher.
    #
    #    Les flexions autorisées sont énumérées explicitement et suivies d'une
    #    frontière de mot. Un suffixe libre (\w*) paraît plus simple mais est
    #    dangereux : il faisait matcher « ban » dans « central bank », si bien
    #    que toute dépêche macro était scorée -2.4 comme une interdiction
    #    réglementaire.
    lexicon = {**BULLISH, **BEARISH}
    for term in sorted(lexicon, key=len, reverse=True):
        consume(rf"\b{re.escape(term)}(?:s|es|ed|ing)?\b", lexicon[term], term)

    # 3) La négation inverse et atténue : « ETF not approved ».
    if re.search(r"\b(not|no|denies|denied|rejects|rejected|fails|failed)\b", low):
        score *= -0.5
    return round(score, 2), matched


def score_geopolitics(text: str) -> tuple[float, list[str]]:
    """Mesure la tension géopolitique d'un texte.

    Positif = escalade (fuite vers la qualité), négatif = apaisement.
    Les deux lexiques, anglais et français, sont appliqués : les flux
    France 24 et Le Monde seraient sinon entièrement ignorés.
    """
    low = f" {text.lower()} "
    score, matched = 0.0, []

    def consume(pattern: str, weight: float, label: str) -> None:
        nonlocal low, score
        new_low, n = re.subn(pattern, " ", low)
        if n:
            low = new_low
            score += weight
            matched.append(label)

    for lexicon in (GEOPOLITICAL, GEOPOLITICAL_FR):
        for term in sorted(lexicon, key=len, reverse=True):
            consume(rf"\b{re.escape(term)}(?:s|es|ed|ing)?\b", lexicon[term], term)

    # Une escalade démentie ou évitée n'est pas une escalade.
    if re.search(r"\b(avoided|averted|denies|denied|ruled out|écarté|démenti)\b", low):
        score *= -0.4
    return round(score, 2), matched


def risk_off_level(articles: list["Article"]) -> dict:
    """Niveau de tension mondiale agrégé, borné dans [-1, +1].

    Seuls les articles porteurs d'un terme géopolitique comptent : une
    dépêche neutre ne doit pas diluer le signal vers zéro, sinon un jour
    calme et un jour de crise deviendraient indiscernables.
    """
    contributors = [a for a in articles if a.risk_terms]
    if not contributors:
        return {"level": 0.0, "raw": 0.0, "count": 0, "top": []}

    weighted = [(a.risk, 0.5 ** (a.age_hours / 12.0)) for a in contributors]
    raw = _weighted(weighted)
    recency = max(w for _, w in weighted)
    level = math.tanh(raw * _count_confidence(len(contributors)) * recency / 3.0)

    top = sorted(contributors, key=lambda a: -abs(a.risk))[:5]
    return {
        "level": round(level, 3),
        "raw": round(raw, 2),
        "count": len(contributors),
        "top": [{"title": a.title, "source": a.source, "risk": a.risk,
                 "terms": a.risk_terms[:4], "url": a.url} for a in top],
    }


def _asset_matches(text: str) -> list[str]:
    low = f" {text.lower()} "
    return [sym for sym, kws in ASSET_KEYWORDS.items() if any(k in low for k in kws)]


def fetch(max_age_hours: float = 36.0, limit: int = 80) -> list[Article]:
    """Récupère et score les articles récents de tous les flux."""
    now = datetime.now(timezone.utc)
    out: list[Article] = []
    seen_titles: set[str] = set()

    for source, url, category in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001 — un flux mort ne doit rien casser
            log.warning("flux %s illisible : %s", source, e)
            continue
        if getattr(feed, "bozo", 0) and not feed.entries:
            log.warning("flux %s vide ou malformé", source)
            continue

        for entry in feed.entries[:40]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            # Le même sujet est repris par plusieurs médias : on déduplique.
            fingerprint = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
            if fingerprint in seen_titles:
                continue

            published = _published_at(entry, now)
            age_h = (now - published).total_seconds() / 3600.0
            if age_h > max_age_hours or age_h < -2:
                continue

            summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or "")[:400]
            blob = f"{title}. {summary}"
            sentiment, matched = score_text(blob)
            risk, risk_terms = score_geopolitics(blob)
            speech = score_speech(blob)
            assets = _asset_matches(blob)
            # Un article sans sentiment, sans tension et sans actif identifié
            # n'apporte rien : on l'écarte plutôt que de gonfler le volume.
            if not matched and not assets and not risk_terms and not speech["terms"]:
                continue

            seen_titles.add(fingerprint)
            out.append(Article(
                title=title[:200], source=source, url=entry.get("link") or "",
                published=published.isoformat(), age_hours=round(age_h, 1),
                sentiment=sentiment, matched=matched[:6], assets=assets,
                category=category, risk=risk, risk_terms=risk_terms[:5],
                speech=speech if speech["terms"] else {},
            ))

    out.sort(key=lambda a: (max(abs(a.sentiment), abs(a.risk)), -a.age_hours),
             reverse=True)
    log.info("news : %d articles retenus sur %d flux", len(out), len(FEEDS))
    return out[:limit]


def sentiment_by_asset(articles: list[Article],
                       symbols: list[str] | None = None) -> dict[str, dict]:
    """Agrège le sentiment par actif, avec décroissance temporelle.

    Deux composantes s'additionnent :

    1. le sentiment des articles citant explicitement l'actif, pondéré par la
       fraîcheur (demi-vie de 12 h), le nombre de sources et la macro ;
    2. le climat géopolitique, appliqué à **tous** les actifs suivis via leur
       bêta de valeur refuge — une escalade au Moyen-Orient ne cite jamais
       « Bitcoin », mais elle le fait baisser et fait monter l'or.

    `symbols` liste les actifs à couvrir. Sans lui, seuls les actifs
    explicitement cités reçoivent un score, et l'axe géopolitique — qui ne
    cite personne — n'atteindrait presque aucun actif.
    """
    buckets: dict[str, list[tuple[float, float]]] = {}
    macro: list[tuple[float, float]] = []

    for a in articles:
        weight = 0.5 ** (a.age_hours / 12.0)
        low = a.title.lower()
        if any(k in low for k in MACRO_KEYWORDS):
            macro.append((a.sentiment, weight))
        for sym in a.assets:
            buckets.setdefault(sym, []).append((a.sentiment, weight))

    macro_score = _weighted(macro) if macro else 0.0
    risk_off = risk_off_level(articles)
    monetary = monetary_stance(articles)

    covered = set(symbols or []) | set(buckets)
    out: dict[str, dict] = {}
    for sym in covered:
        items = buckets.get(sym, [])
        if items:
            raw = _weighted(items)
            # `_weighted` normalise par la somme des poids : la décroissance
            # temporelle y règle l'importance *relative* des articles entre
            # eux, mais se simplifie entièrement quand il n'y en a qu'un.
            # Sans le facteur de fraîcheur ci-dessous, une dépêche isolée de
            # 36 heures pèserait autant qu'une dépêche d'il y a une heure.
            recency = max(w for _, w in items)
            direct = ((raw + MACRO_WEIGHT * macro_score)
                      * _count_confidence(len(items)) * recency)
        else:
            raw, recency, direct = 0.0, 0.0, 0.0

        beta = HAVEN_BETA.get(sym, DEFAULT_HAVEN_BETA)
        geo = risk_off["level"] * beta * GEO_WEIGHT
        mbeta = MONETARY_BETA.get(sym, DEFAULT_MONETARY_BETA)
        mon = monetary["stance"] * mbeta * MONETARY_WEIGHT

        out[sym] = {
            # tanh borne le score dans [-1, 1] sans seuil arbitraire : une
            # avalanche de news ne peut pas dominer le score technique.
            "score": round(float(np.clip(math.tanh(direct / 4.0) + geo + mon, -1.0, 1.0)), 3),
            "raw": round(raw, 2),
            "macro": round(macro_score, 2),
            "count": len(items),
            "confidence": round(_count_confidence(len(items)), 2) if items else 0.0,
            "recency": round(recency, 3),
            "geo": round(geo, 3),
            "haven_beta": beta,
            "monetary": round(mon, 3),
            "monetary_beta": mbeta,
        }
    return out


def _weighted(items: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in items)
    return sum(s * w for s, w in items) / total_w if total_w > 0 else 0.0


def _published_at(entry, now: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return now
