"""Structure de marché et optimisation des niveaux d'entrée, de stop et d'objectif.

Un stop à « 2 × ATR » est simple mais naïf : il ignore complètement où se
trouvent les niveaux que le marché respecte réellement. Un stop placé juste
au-dessus d'un creux structurel sera balayé par le premier balayage de
liquidité ; le même stop placé légèrement en dessous survit au bruit et
n'est touché que si la thèse est réellement invalidée.

Ce module procède en trois temps :

1. il identifie la structure — points pivots, zones de congestion, niveaux
   de Fibonacci, point de contrôle du volume ;
2. il génère des couples (stop, objectif) candidats, adossés à cette
   structure plutôt qu'à un multiple arbitraire ;
3. il retient le couple qui maximise l'espérance mathématique, estimée par
   un modèle de ruine du joueur corrigé de l'avantage du signal.

Le point clé du troisième temps : pour une marche aléatoire sans dérive, la
probabilité d'atteindre l'objectif avant le stop vaut exactement
`d_stop / (d_stop + d_objectif)`, ce qui rend l'espérance **nulle quel que
soit le ratio rendement/risque choisi**. Aucun réglage de R/R ne crée
d'avantage : celui-ci ne peut venir que de la qualité du signal. Le modèle
rend donc explicite quelque chose que la plupart des systèmes masquent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import indicators as I
from . import stats as S

log = logging.getLogger("jimbot.levels")

# Distance minimale et maximale du stop, en ATR. En deçà, le bruit ordinaire
# suffit à le déclencher ; au-delà, la position devient trop petite pour que
# le trade ait un intérêt.
MIN_STOP_ATR = 0.8
MAX_STOP_ATR = 3.5

# Marge appliquée au-delà du niveau structurel retenu. Les stops massés
# exactement sur un plus-bas visible sont la cible privilégiée des balayages
# de liquidité : on se place derrière, pas dessus.
STOP_BUFFER_ATR = 0.25

# Bornes du ratio rendement/risque exploré.
MIN_RR = 1.0
MAX_RR = 5.0

# Avantage maximal, en points de probabilité, qu'un signal parfait (score 100)
# peut ajouter à la probabilité de ruine du joueur. Volontairement modeste :
# prétendre qu'un score technique déplace la probabilité de plus de 18 points
# serait malhonnête.
MAX_EDGE = 0.18

# Horizon de l'avantage, en ATR. C'est le paramètre décisif de tout le module.
#
# Traiter l'avantage comme constant quelle que soit la distance de l'objectif
# est mathématiquement ruineux : avec `p = d_stop/(d_stop+d_obj) + e`,
# l'espérance se simplifie en `E = e × (R/R + 1)`, strictement croissante en
# R/R. L'optimiseur retiendrait alors toujours le R/R maximal autorisé, et
# choisirait donc un objectif d'autant meilleur qu'il est lointain — ce qui
# est manifestement faux.
#
# L'erreur est dans l'hypothèse : l'information directionnelle d'un signal
# technique porte sur les prochaines bougies, pas sur un mouvement
# arbitrairement lointain. On la fait donc décroître exponentiellement avec
# la distance de l'objectif, ce qui restitue un optimum intérieur.
#
# Ordre de grandeur : un mouvement de 5 ATR demande une vingtaine de bougies
# (l'amplitude cumulée croît en racine du temps), horizon au-delà duquel un
# signal horaire n'a plus grand-chose à dire.
EDGE_HORIZON_ATR = 5.0

# Le modèle de ruine du joueur est invariant d'échelle : à ratio
# rendement/risque égal, il juge un stop à 1 ATR aussi sûr qu'un stop à 3 ATR.
# C'est faux dans les deux sens, et les trois corrections ci-dessous rétablissent
# ce que la structure apporte réellement.

# 1. Un stop adossé à un niveau réellement respecté est moins souvent touché
#    que ne le prédit une marche aléatoire : le niveau dévie le prix. Bonus
#    maximal, en points de probabilité, pour un support de solidité 1.
STRUCTURE_EDGE = 0.10

# 2. En deçà de ce seuil, le stop est dans le bruit ordinaire de la bougie et
#    sera touché par une simple mèche, indépendamment de la thèse.
NOISE_FLOOR_ATR = 1.6
NOISE_PENALTY = 0.22

# 3. Un objectif qui exige de traverser une résistance solide est moins
#    accessible qu'un objectif en espace dégagé, à distance égale.
OBSTACLE_PENALTY = 0.13


@dataclass
class Level:
    """Un niveau de prix identifié dans la structure."""

    price: float
    kind: str        # "pivot_haut" | "pivot_bas" | "congestion" | "fibonacci" | "vpoc"
    strength: float  # 0-1, robustesse du niveau
    touches: int     # nombre de fois où le prix y a réagi

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Plan:
    """Un plan de trade complet, avec sa justification chiffrée."""

    entry: float
    stop: float
    target: float
    rr: float
    stop_atr: float        # distance du stop, en ATR
    win_prob: float        # probabilité estimée d'atteindre l'objectif d'abord
    expected_r: float      # espérance en multiples de risque
    stop_basis: str        # niveau structurel ayant justifié le stop
    target_basis: str      # niveau structurel ayant justifié l'objectif
    alternatives: list[dict]  # autres couples évalués, pour audit

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Détection de structure
# --------------------------------------------------------------------------
def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[list[Level], list[Level]]:
    """Points pivots : un plus-haut entouré de `left`/`right` bougies plus basses.

    Le paramètre `right` impose une confirmation : un pivot n'existe qu'une
    fois que le marché s'en est éloigné. Sans lui, la dernière bougie serait
    toujours un pivot, ce qui produirait des niveaux fantômes.
    """
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    highs: list[Level] = []
    lows: list[Level] = []

    for i in range(left, n - right):
        window_h = high[i - left: i + right + 1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            # Plus le pivot est récent, plus il compte : la structure ancienne
            # est souvent invalidée.
            recency = (i / n) ** 0.5
            highs.append(Level(float(high[i]), "pivot_haut", round(recency, 3), 1))
        window_l = low[i - left: i + right + 1]
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            recency = (i / n) ** 0.5
            lows.append(Level(float(low[i]), "pivot_bas", round(recency, 3), 1))

    return highs, lows


def cluster_levels(levels: list[Level], tolerance: float) -> list[Level]:
    """Fusionne les niveaux proches en zones de congestion.

    Trois pivots à 0.3 % les uns des autres ne sont pas trois niveaux mais un
    seul, trois fois plus solide. C'est le nombre de contacts qui fait la
    force d'un support, pas son existence.
    """
    if not levels:
        return []
    ordered = sorted(levels, key=lambda l: l.price)
    clusters: list[list[Level]] = [[ordered[0]]]

    for lvl in ordered[1:]:
        if abs(lvl.price - clusters[-1][-1].price) <= tolerance:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])

    merged = []
    for group in clusters:
        # Le prix de la zone est la moyenne pondérée par la robustesse.
        total_w = sum(l.strength for l in group) or 1.0
        price = sum(l.price * l.strength for l in group) / total_w
        touches = len(group)
        # La force croît avec le nombre de contacts, mais sature : un niveau
        # touché dix fois n'est pas dix fois plus solide qu'un niveau touché
        # deux fois — et un niveau trop souvent touché finit par céder.
        strength = min(1.0, (max(l.strength for l in group) * 0.6
                             + 0.4 * np.log1p(touches) / np.log(5)))
        kind = "congestion" if touches > 1 else group[0].kind
        merged.append(Level(round(float(price), 8), kind, round(float(strength), 3), touches))
    return merged


def fibonacci_levels(df: pd.DataFrame, lookback: int = 120) -> list[Level]:
    """Retracements et extensions de Fibonacci sur l'impulsion dominante.

    Ces niveaux n'ont aucune justification physique ; ils comptent parce
    qu'une partie significative des participants les surveille, ce qui les
    rend partiellement auto-réalisateurs. On leur attribue donc une
    robustesse modérée, jamais dominante.
    """
    window = df.tail(lookback)
    if len(window) < 20:
        return []

    hi = float(window["high"].max())
    lo = float(window["low"].min())
    span = hi - lo
    if span <= 0:
        return []

    # Sens de l'impulsion : le plus-haut est-il postérieur au plus-bas ?
    up = window["high"].idxmax() > window["low"].idxmin()
    out = []
    for ratio, strength in ((0.382, 0.45), (0.5, 0.5), (0.618, 0.6), (0.786, 0.45)):
        price = hi - span * ratio if up else lo + span * ratio
        out.append(Level(round(price, 8), "fibonacci", strength, 1))
    for ratio, strength in ((1.272, 0.4), (1.618, 0.45)):
        price = lo + span * ratio if up else hi - span * ratio
        out.append(Level(round(price, 8), "fibonacci", strength, 1))
    return out


def volume_poc(df: pd.DataFrame, lookback: int = 150, bins: int = 40) -> Level | None:
    """Point de contrôle : prix auquel le plus gros volume s'est échangé.

    C'est le prix d'équilibre reconnu par le marché sur la période : il agit
    comme un aimant, et comme un support ou une résistance solide selon le
    côté d'où le prix l'aborde.
    """
    window = df.tail(lookback)
    if len(window) < 30 or window["volume"].sum() <= 0:
        return None

    typical = (window["high"] + window["low"] + window["close"]) / 3.0
    hist, edges = np.histogram(typical, bins=bins, weights=window["volume"])
    if hist.sum() <= 0:
        return None
    idx = int(hist.argmax())
    price = float((edges[idx] + edges[idx + 1]) / 2.0)
    # Concentration du volume au POC par rapport au reste : plus elle est
    # forte, plus le niveau est significatif.
    concentration = float(hist[idx] / hist.sum())
    strength = min(1.0, concentration * bins / 3.0)
    return Level(round(price, 8), "vpoc", round(strength, 3), 1)


def build_structure(df: pd.DataFrame) -> dict[str, list[Level]]:
    """Assemble tous les niveaux structurels, séparés en supports et résistances."""
    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    if not np.isfinite(atr_v) or atr_v <= 0 or price <= 0:
        return {"supports": [], "resistances": []}

    highs, lows = swing_points(df)
    tolerance = atr_v * 0.6

    candidates = cluster_levels(highs + lows, tolerance)
    candidates += fibonacci_levels(df)
    poc = volume_poc(df)
    if poc is not None:
        candidates.append(poc)

    # Un niveau au-delà de 6 ATR n'a aucune pertinence opérationnelle.
    horizon = atr_v * 6.0
    supports = sorted([l for l in candidates if 0 < price - l.price <= horizon],
                      key=lambda l: -l.price)
    resistances = sorted([l for l in candidates if 0 < l.price - price <= horizon],
                         key=lambda l: l.price)
    return {"supports": supports, "resistances": resistances}


# --------------------------------------------------------------------------
# Espérance mathématique
# --------------------------------------------------------------------------
def win_probability(stop_dist: float, target_dist: float, score: float,
                    regime_quality: float = 0.5, atr: float = 0.0,
                    *, stop_strength: float = 0.0,
                    obstacle: float = 0.0) -> float:
    """Probabilité d'atteindre l'objectif avant le stop.

    Le point de départ est le résultat exact de la ruine du joueur pour une
    marche aléatoire sans dérive : `p = d_stop / (d_stop + d_objectif)`.
    Cette base rend l'espérance rigoureusement nulle quel que soit le ratio
    rendement/risque — autrement dit, **aucun réglage de R/R ne crée
    d'avantage à lui seul**. L'avantage ne peut venir que de la capacité du
    signal à prédire la direction, et de la structure du marché.

    Quatre corrections s'ajoutent à cette base :

    - l'avantage du signal, proportionnel à la conviction et à la qualité du
      régime, **décroissant avec la distance de l'objectif** : sans cette
      décroissance, l'espérance croîtrait indéfiniment avec le R/R ;
    - un bonus si le stop est adossé à un niveau structurel solide ;
    - une pénalité si le stop est dans le bruit ordinaire de la bougie ;
    - une pénalité si l'objectif exige de traverser une résistance.

    Sans les trois dernières, le modèle est invariant d'échelle et retient
    toujours le stop le plus serré possible, la structure n'ayant alors
    aucune influence sur le résultat.
    """
    total = stop_dist + target_dist
    if total <= 0:
        return 0.0
    p_base = stop_dist / total

    # Le score n'apporte un avantage qu'au-delà du seuil de neutralité : à 50,
    # le signal ne dit rien, et l'avantage doit être nul.
    conviction = max(0.0, (score - 50.0) / 50.0)
    edge = MAX_EDGE * conviction * float(np.clip(regime_quality, 0.0, 1.0))
    if atr > 0:
        edge *= float(np.exp(-(target_dist / atr) / EDGE_HORIZON_ATR))

    structure_bonus = STRUCTURE_EDGE * float(np.clip(stop_strength, 0.0, 1.0))

    noise_penalty = 0.0
    if atr > 0:
        shortfall = max(0.0, NOISE_FLOOR_ATR - stop_dist / atr) / NOISE_FLOOR_ATR
        # Quadratique : un stop légèrement trop serré est peu pénalisé, un
        # stop très serré l'est fortement.
        noise_penalty = NOISE_PENALTY * shortfall ** 2

    obstacle_penalty = OBSTACLE_PENALTY * float(np.clip(obstacle, 0.0, 1.5))

    p = p_base + edge + structure_bonus - noise_penalty - obstacle_penalty
    return float(np.clip(p, 0.02, 0.98))


def expected_r(win_prob: float, rr: float) -> float:
    """Espérance du trade en multiples de risque.

    Convention : une perte coûte exactement 1 R, un gain rapporte `rr` R.
    """
    return win_prob * rr - (1.0 - win_prob)


# --------------------------------------------------------------------------
# Optimisation du plan
# --------------------------------------------------------------------------
def optimal_plan(df: pd.DataFrame, direction: str, score: float,
                 *, regime_quality: float = 0.5,
                 fallback_atr_mult: float = 2.0,
                 fallback_rr: float = 2.0) -> Plan:
    """Construit le plan de trade maximisant l'espérance mathématique.

    La recherche est contrainte par la structure : les stops candidats sont
    adossés à des niveaux réellement respectés par le marché, et les
    objectifs sont bornés par les obstacles qui se trouvent devant. On ne
    choisit pas un R/R en l'air pour ensuite le plaquer sur le graphique.
    """
    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    if not np.isfinite(atr_v) or atr_v <= 0 or price <= 0 or direction == "neutre":
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult, fallback_rr)

    structure = build_structure(df)
    # Pour un achat, le stop se place sous un support et l'objectif sous une
    # résistance ; pour une vente, tout est inversé.
    stop_side = structure["supports"] if direction == "long" else structure["resistances"]
    target_side = structure["resistances"] if direction == "long" else structure["supports"]

    stop_candidates = _stop_candidates(price, atr_v, direction, stop_side)
    if not stop_candidates:
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult, fallback_rr)

    evaluated: list[Plan] = []
    for stop, stop_dist, stop_basis, stop_strength in stop_candidates:
        for target, rr, target_basis, obstacle in _target_candidates(
                price, atr_v, direction, target_side, stop_dist):
            p = win_probability(stop_dist, abs(target - price), score,
                                regime_quality, atr_v,
                                stop_strength=stop_strength, obstacle=obstacle)
            ev = expected_r(p, rr)
            evaluated.append(Plan(
                entry=round(price, 8), stop=round(stop, 8), target=round(target, 8),
                rr=round(rr, 2), stop_atr=round(stop_dist / atr_v, 2),
                win_prob=round(p, 3), expected_r=round(ev, 3),
                stop_basis=stop_basis, target_basis=target_basis, alternatives=[]))

    if not evaluated:
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult, fallback_rr)

    # Plusieurs stops candidats peuvent produire des couples (stop, objectif)
    # identiques : on déduplique avant de classer, sinon la liste
    # d'alternatives n'affiche que des doublons du plan retenu.
    unique: dict[tuple[float, float], Plan] = {}
    for pl in evaluated:
        key = (round(pl.stop, 8), round(pl.target, 8))
        if key not in unique or pl.expected_r > unique[key].expected_r:
            unique[key] = pl
    evaluated = sorted(unique.values(), key=lambda pl: -pl.expected_r)
    best = evaluated[0]
    # On conserve les meilleures alternatives : le rapport peut ainsi montrer
    # que le plan retenu a été choisi, et non simplement produit.
    best.alternatives = [
        {"stop": p.stop, "target": p.target, "rr": p.rr, "win_prob": p.win_prob,
         "expected_r": p.expected_r, "stop_basis": p.stop_basis,
         "target_basis": p.target_basis}
        for p in evaluated[1:5]
    ]
    log.debug("plan retenu : R/R %.2f, p=%.2f, espérance %+.3f R (%s / %s)",
              best.rr, best.win_prob, best.expected_r, best.stop_basis, best.target_basis)
    return best


def _stop_candidates(price: float, atr_v: float, direction: str,
                     levels: list[Level]) -> list[tuple[float, float, str, float]]:
    """Stops adossés à la structure, plus un repli purement volatilité.

    Chaque candidat porte la solidité du niveau qui le justifie ; un repli en
    pure volatilité a une solidité nulle, n'étant adossé à rien.
    """
    out: list[tuple[float, float, str, float]] = []
    sign = -1.0 if direction == "long" else 1.0

    for lvl in levels[:6]:
        # On se place derrière le niveau, jamais dessus.
        stop = lvl.price + sign * STOP_BUFFER_ATR * atr_v
        dist = abs(price - stop)
        if not (MIN_STOP_ATR * atr_v <= dist <= MAX_STOP_ATR * atr_v):
            continue
        label = (f"{lvl.kind.replace('_', ' ')} à {lvl.price:.6g}"
                 f" ({lvl.touches} contact{'s' if lvl.touches > 1 else ''},"
                 f" solidité {lvl.strength:.2f})")
        # Seuls les niveaux réellement testés dévient le prix : un pivot
        # unique ou un ratio de Fibonacci ne valent pas une congestion.
        effective = lvl.strength if lvl.touches >= 2 else lvl.strength * 0.4
        out.append((stop, dist, label, effective))

    # Repli systématique : si la structure est absente ou inexploitable, un
    # stop en pure volatilité vaut mieux que pas de stop du tout.
    for mult in (1.5, 2.0, 2.5):
        dist = mult * atr_v
        out.append((price + sign * dist, dist, f"volatilité pure ({mult:.1f} ATR)", 0.0))
    return out


def _obstacle_between(price: float, target: float, levels: list[Level]) -> float:
    """Somme des solidités des niveaux situés entre le prix et l'objectif.

    Viser au-delà d'une résistance solide, c'est parier que le marché
    traverse un niveau qu'il a déjà respecté — c'est possible, mais moins
    probable qu'un parcours dégagé, et le modèle doit le refléter.
    """
    lo, hi = min(price, target), max(price, target)
    return sum(l.strength for l in levels if lo < l.price < hi)


def _target_candidates(price: float, atr_v: float, direction: str,
                       levels: list[Level], stop_dist: float
                       ) -> list[tuple[float, float, str, float]]:
    """Objectifs bornés par les obstacles structurels devant le prix."""
    out: list[tuple[float, float, str, float]] = []
    sign = 1.0 if direction == "long" else -1.0

    for lvl in levels[:6]:
        # On vise juste avant l'obstacle : viser au-delà, c'est espérer que le
        # marché traverse un niveau qu'il a déjà respecté.
        target = lvl.price - sign * STOP_BUFFER_ATR * atr_v
        dist = abs(target - price)
        rr = dist / stop_dist if stop_dist > 0 else 0.0
        if not (MIN_RR <= rr <= MAX_RR):
            continue
        label = (f"{lvl.kind.replace('_', ' ')} à {lvl.price:.6g}"
                 f" (solidité {lvl.strength:.2f})")
        # L'objectif est placé juste avant ce niveau : l'obstacle compté est
        # donc celui des niveaux antérieurs uniquement.
        out.append((target, rr, label, _obstacle_between(price, target, levels)))

    # Objectifs en multiples de risque, indépendants de la structure : ils
    # servent de référence et couvrent le cas d'un espace dégagé devant.
    for rr in (1.5, 2.0, 2.5, 3.0):
        dist = rr * stop_dist
        target = price + sign * dist
        obstacle = _obstacle_between(price, target, levels)
        # Ces objectifs ignorent la structure par construction : s'il y a des
        # résistances sur le chemin, la pénalité d'obstacle les disqualifiera.
        qualif = "espace dégagé" if obstacle < 0.3 else f"{obstacle:.1f} de résistance à franchir"
        out.append((target, rr, f"multiple de risque ({rr:.1f} R, {qualif})", obstacle))
    return out


def _fallback_plan(price: float, atr_v: float, direction: str,
                   atr_mult: float, rr_target: float) -> Plan:
    """Plan de repli en pure volatilité, quand la structure est inexploitable."""
    if direction == "neutre" or not np.isfinite(atr_v) or atr_v <= 0 or price <= 0:
        return Plan(round(price, 8), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    "aucun", "aucun", [])
    dist = atr_mult * atr_v
    sign = -1.0 if direction == "long" else 1.0
    stop = price + sign * dist
    target = price - sign * dist * rr_target
    p = win_probability(dist, dist * rr_target, 50.0)
    return Plan(round(price, 8), round(stop, 8), round(target, 8), round(rr_target, 2),
                round(atr_mult, 2), round(p, 3), round(expected_r(p, rr_target), 3),
                f"volatilité pure ({atr_mult:.1f} ATR)",
                f"multiple de risque ({rr_target:.1f} R)", [])
