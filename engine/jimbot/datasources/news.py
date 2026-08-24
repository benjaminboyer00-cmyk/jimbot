"""Actualités via flux RSS publics + scoring de sentiment déterministe.

Le sentiment est calculé par lexique pondéré, pas par LLM : c'est
reproductible, gratuit, instantané, et surtout auditable — on peut toujours
dire quels mots ont produit quel score. Le LLM n'intervient qu'en aval, pour
rédiger, jamais pour chiffrer.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import feedparser

log = logging.getLogger("jimbot.data.news")

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("TheBlock", "https://www.theblock.co/rss.xml"),
    ("CNBC Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss25&id=20910258"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
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
    sentiment: float    # score brut, non borné
    matched: list[str]  # termes du lexique déclenchés
    assets: list[str]   # symboles concernés

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


def _asset_matches(text: str) -> list[str]:
    low = f" {text.lower()} "
    return [sym for sym, kws in ASSET_KEYWORDS.items() if any(k in low for k in kws)]


def fetch(max_age_hours: float = 36.0, limit: int = 80) -> list[Article]:
    """Récupère et score les articles récents de tous les flux."""
    now = datetime.now(timezone.utc)
    out: list[Article] = []
    seen_titles: set[str] = set()

    for source, url in FEEDS:
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
            assets = _asset_matches(blob)
            if not matched and not assets:
                continue  # ni sentiment ni actif identifié : sans valeur

            seen_titles.add(fingerprint)
            out.append(Article(
                title=title[:200], source=source, url=entry.get("link") or "",
                published=published.isoformat(), age_hours=round(age_h, 1),
                sentiment=sentiment, matched=matched[:6], assets=assets,
            ))

    out.sort(key=lambda a: (abs(a.sentiment), -a.age_hours), reverse=True)
    log.info("news : %d articles retenus sur %d flux", len(out), len(FEEDS))
    return out[:limit]


def sentiment_by_asset(articles: list[Article]) -> dict[str, dict]:
    """Agrège le sentiment par actif, avec décroissance temporelle.

    Une nouvelle de 30 h pèse beaucoup moins qu'une nouvelle d'une heure :
    on applique une demi-vie de 12 h.
    """
    import math

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
    out: dict[str, dict] = {}
    for sym, items in buckets.items():
        raw = _weighted(items)
        # `_weighted` normalise par la somme des poids : la décroissance
        # temporelle y règle l'importance *relative* des articles entre eux,
        # mais se simplifie entièrement quand il n'y en a qu'un. Sans le
        # facteur de fraîcheur ci-dessous, une dépêche isolée de 36 heures
        # pèserait autant qu'une dépêche d'il y a une heure.
        # On retient le poids du plus récent : c'est lui qui dit si le sujet
        # est encore vivant.
        recency = max(w for _, w in items)
        blended = ((raw + MACRO_WEIGHT * macro_score)
                   * _count_confidence(len(items)) * recency)
        out[sym] = {
            # tanh borne le score dans [-1, 1] sans seuil arbitraire : une
            # avalanche de news ne peut pas dominer le score technique.
            "score": round(math.tanh(blended / 4.0), 3),
            "raw": round(raw, 2),
            "macro": round(macro_score, 2),
            "count": len(items),
            "confidence": round(_count_confidence(len(items)), 2),
            "recency": round(recency, 3),
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
