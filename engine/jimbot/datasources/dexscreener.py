"""Memecoins via DexScreener (gratuit, sans clé).

Le marché des memecoins est majoritairement composé de pièges : pools sans
liquidité, honeypots, tokens créés il y a dix minutes et abandonnés. Ce
module ne cherche pas à prédire un rendement, il applique d'abord un filtre
de survie strict, et ne laisse passer que ce qui est structurellement
échangeable. Tout ce qui échoue au filtre est écarté sans discussion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

from .base import DataError, http_get_json

log = logging.getLogger("jimbot.data.dex")

BASE = "https://api.dexscreener.com/latest/dex"

# --- Seuils du filtre anti-rug ---
# Calibrés sur la population réelle renvoyée par les endpoints de tendance
# (mesuré : sur 12 tokens boostés, 2 passent — le filtre est censé éliminer
# la grande majorité, c'est son rôle).
MIN_LIQUIDITY_USD = 50_000       # sous ce seuil, une position de 5 000 $ déplace le prix de plus de 10 %
MIN_VOLUME_24H_USD = 250_000     # sans volume, pas d'exécution
MIN_AGE_HOURS = 24               # les 24 premières heures concentrent les rugs
MIN_TXNS_24H = 400               # activité réelle, pas du wash trading à deux adresses
MAX_VOL_LIQ_RATIO = 40.0         # volume >> liquidité = rotation artificielle
MIN_VOL_LIQ_RATIO = 0.4          # liquidité inerte

# Endpoints de découverte. `/search` ne convient pas : interrogé avec un nom de
# jeton, il renvoie tous les pools de CE jeton (dont des pools morts à 4 $ de
# volume), pas une sélection de memecoins différents.
BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKENS_URL = "https://api.dexscreener.com/tokens/v1"
MAX_ADDRESSES_PER_CALL = 25


@dataclass
class MemePair:
    """Une paire DEX ayant passé le filtre de survie."""

    symbol: str
    name: str
    chain: str
    address: str
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    change_1h: float
    change_6h: float
    change_24h: float
    txns_24h: int
    age_hours: float
    buy_sell_ratio: float
    url: str

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def health_score(self) -> float:
        """Score de robustesse structurelle 0-100 (pas une prédiction de prix).

        Mesure uniquement : profondeur du carnet, activité, maturité,
        équilibre acheteurs/vendeurs. Un score élevé signifie « négociable »,
        pas « va monter ».
        """
        import math

        # Liquidité et volume comptent en échelle log : passer de 100k à 200k
        # change beaucoup, de 5M à 5.1M ne change rien.
        liq = min(1.0, math.log10(max(self.liquidity_usd, 1)) / 7.0)
        vol = min(1.0, math.log10(max(self.volume_24h, 1)) / 7.5)
        txn = min(1.0, math.log10(max(self.txns_24h, 1)) / 4.5)
        age = min(1.0, self.age_hours / (24 * 30))
        # Un ratio achats/ventes proche de 1 est sain ; un déséquilibre extrême
        # signale soit une distribution, soit un pump artificiel.
        balance = 1.0 - min(1.0, abs(self.buy_sell_ratio - 1.0) / 1.5)
        score = 100 * (0.30 * liq + 0.25 * vol + 0.20 * txn + 0.10 * age + 0.15 * balance)
        return round(score, 1)


# Paliers de maturité : un pool jeune n'est pas interdit, il doit compenser
# son manque d'historique par une profondeur et une activité supérieures.
# Un pool de 8 heures avec 3 M$ de volume et 90 k$ de liquidité reste un pari
# sur la bonne foi du déployeur ; à 150 k$ de liquidité, la sortie est au
# moins possible.
#            âge min (h), liquidité min $, volume 24h min $, transactions min
TIERS = [
    (168.0, 50_000, 250_000, 400),      # au-delà d'une semaine : palier de base
    (72.0, 65_000, 400_000, 800),       # 3 à 7 jours
    (24.0, 90_000, 700_000, 1_500),     # 1 à 3 jours
    (8.0, 150_000, 1_500_000, 3_000),   # 8 à 24 heures : exigences maximales
]


def _passes_filter(p: MemePair) -> tuple[bool, str]:
    """Filtre de survie à paliers. Renvoie (accepté, raison du rejet)."""
    if p.age_hours < TIERS[-1][0]:
        return False, f"pool âgé de {p.age_hours:.0f}h, sous les {TIERS[-1][0]:.0f}h requises"

    # Premier palier dont l'âge minimal est atteint.
    min_liq, min_vol, min_tx = next(
        (liq, vol, tx) for age, liq, vol, tx in TIERS if p.age_hours >= age)

    if p.liquidity_usd < min_liq:
        return False, (f"liquidité {p.liquidity_usd:,.0f}$ < {min_liq:,}$ "
                       f"requis à {p.age_hours:.0f}h d'âge")
    if p.volume_24h < min_vol:
        return False, (f"volume 24h {p.volume_24h:,.0f}$ < {min_vol:,}$ "
                       f"requis à {p.age_hours:.0f}h d'âge")
    if p.txns_24h < min_tx:
        return False, f"{p.txns_24h} transactions 24h < {min_tx} requises"

    ratio = p.volume_24h / max(p.liquidity_usd, 1.0)
    if ratio > MAX_VOL_LIQ_RATIO:
        return False, f"ratio volume/liquidité {ratio:.0f}x, rotation artificielle"
    if ratio < MIN_VOL_LIQ_RATIO:
        return False, f"ratio volume/liquidité {ratio:.2f}x, liquidité inerte"
    return True, ""


def _parse(raw: dict) -> MemePair | None:
    """Convertit une paire brute DexScreener en MemePair, ou None si inexploitable."""
    import time

    try:
        base = raw.get("baseToken") or {}
        liq = (raw.get("liquidity") or {}).get("usd") or 0.0
        vol = (raw.get("volume") or {}).get("h24") or 0.0
        chg = raw.get("priceChange") or {}
        txns = raw.get("txns") or {}
        h24 = txns.get("h24") or {}
        buys, sells = int(h24.get("buys") or 0), int(h24.get("sells") or 0)

        created_ms = raw.get("pairCreatedAt")
        age_h = ((time.time() * 1000 - created_ms) / 3_600_000) if created_ms else 0.0

        return MemePair(
            symbol=str(base.get("symbol") or "?").upper()[:16],
            name=str(base.get("name") or "?")[:48],
            chain=str(raw.get("chainId") or "?"),
            address=str(base.get("address") or ""),
            price_usd=float(raw.get("priceUsd") or 0.0),
            liquidity_usd=float(liq),
            volume_24h=float(vol),
            change_1h=float(chg.get("h1") or 0.0),
            change_6h=float(chg.get("h6") or 0.0),
            change_24h=float(chg.get("h24") or 0.0),
            txns_24h=buys + sells,
            age_hours=float(age_h),
            buy_sell_ratio=(buys / sells) if sells > 0 else (2.0 if buys else 1.0),
            url=str(raw.get("url") or ""),
        )
    except (TypeError, ValueError) as e:
        log.debug("paire ignorée, parsing impossible : %s", e)
        return None


def _candidates(chains: list[str]) -> dict[str, list[str]]:
    """Adresses de jetons en tendance, groupées par chaîne.

    Deux sources complémentaires : les jetons boostés (visibilité payée, donc
    activité en cours) et les profils récemment publiés (nouveaux entrants).
    """
    out: dict[str, list[str]] = {c: [] for c in chains}
    for url in (BOOSTS_URL, PROFILES_URL):
        try:
            entries = http_get_json(url)
        except DataError as e:
            log.warning("découverte %s indisponible : %s", url.rsplit("/", 2)[-2], e)
            continue
        if not isinstance(entries, list):
            continue
        for e in entries:
            chain, addr = e.get("chainId"), e.get("tokenAddress")
            if chain in out and addr and addr not in out[chain]:
                out[chain].append(addr)
    return out


def screen(chains: list[str], limit: int = 12) -> list[MemePair]:
    """Découvre les memecoins en tendance et applique le filtre de survie."""
    retained, _ = screen_detailed(chains, limit)
    return retained


def screen_detailed(chains: list[str], limit: int = 12) -> tuple[list[MemePair], dict]:
    """Comme `screen`, mais renvoie aussi le compte-rendu du criblage.

    Un cycle sans aucun jeton retenu est le cas normal, pas une panne : la
    liste des jetons boostés est très majoritairement composée de pools trop
    peu liquides pour être vendus. Le compte-rendu permet de l'afficher
    honnêtement dans le rapport au lieu de laisser une section vide.
    """
    best: dict[str, MemePair] = {}
    rejected: dict[str, tuple[MemePair, str]] = {}

    for chain, addresses in _candidates(chains).items():
        for i in range(0, len(addresses), MAX_ADDRESSES_PER_CALL):
            batch = addresses[i:i + MAX_ADDRESSES_PER_CALL]
            try:
                pairs = http_get_json(f"{TOKENS_URL}/{chain}/{','.join(batch)}")
            except DataError as e:
                log.warning("paires %s indisponibles : %s", chain, e)
                continue
            if not isinstance(pairs, list):
                continue

            for entry in pairs:
                pair = _parse(entry)
                if pair is None or pair.price_usd <= 0 or not pair.address:
                    continue
                key = f"{pair.chain}:{pair.address}"
                # Seul le pool le plus profond de chaque jeton compte : c'est
                # celui sur lequel un ordre s'exécuterait réellement.
                if key in best and best[key].liquidity_usd >= pair.liquidity_usd:
                    continue
                ok, reason = _passes_filter(pair)
                if ok:
                    best[key] = pair
                    rejected.pop(key, None)
                elif key not in best and (
                        key not in rejected
                        or pair.liquidity_usd > rejected[key][0].liquidity_usd):
                    rejected[key] = (pair, reason)

    # Les quasi-retenus, classés par liquidité : ce sont eux qu'il faut
    # surveiller au prochain cycle.
    near = sorted(rejected.values(), key=lambda x: -x[0].liquidity_usd)[:6]
    report = {
        "screened": len(best) + len(rejected),
        "retained": len(best),
        "rejected": len(rejected),
        "near_misses": [{"symbol": p.symbol, "chain": p.chain,
                         "liquidity_usd": round(p.liquidity_usd),
                         "volume_24h": round(p.volume_24h),
                         "age_hours": round(p.age_hours, 1),
                         "reason": r} for p, r in near],
    }
    log.info("DexScreener : %d jeton(s) retenu(s) sur %d criblé(s)",
             report["retained"], report["screened"])
    return sorted(best.values(), key=lambda p: p.health_score, reverse=True)[:limit], report
