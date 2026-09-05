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
# Bornes revues à la hausse d'après la mesure. Le coût de transaction rapporté
# au risque vaut `frais × prix / distance_au_stop` : un stop deux fois plus
# large le divise par deux. Or l'avantage mesuré (~3 points de probabilité)
# est du même ordre que les frais sur un stop de 2 ATR — c'est ce qui rendait
# presque tous les plans non rentables. Élargir la fourchette laisse
# l'optimiseur trouver le point où l'avantage dépasse les coûts.
MIN_STOP_ATR = 1.2
MAX_STOP_ATR = 5.0

# Marge appliquée au-delà du niveau structurel retenu. Les stops massés
# exactement sur un plus-bas visible sont la cible privilégiée des balayages
# de liquidité : on se place derrière, pas dessus.
STOP_BUFFER_ATR = 0.25

# Bornes du ratio rendement/risque exploré.
MIN_RR = 1.0
MAX_RR = 5.0

# Avantage maximal, en points de probabilité, qu'un signal parfait peut
# ajouter à la probabilité de ruine du joueur.
#
# Histoire de cette constante, qui résume la démarche du projet :
#
# - 0.18 au départ, posée a priori, sans aucune mesure ;
# - ramenée à 0.02 quand le backtest a montré que le moteur réalisait 35.6 %
#   de réussite pour un seuil de rentabilité sans avantage de 35.9 % — le
#   signal n'apportait rien ;
# - remontée à 0.12 après que la sonde de pouvoir prédictif a révélé que les
#   facteurs de suivi de tendance prédisaient à l'envers. Une fois leurs
#   signes corrigés, le score au-delà du seuil précède un rendement supérieur
#   de +0.155 ATR à la moyenne sur 24 bougies, soit environ 3.1 points de
#   probabilité pour un stop de 2 ATR et un objectif de 3 ATR.
#
# La valeur retenue applique une décote de moitié à cette mesure : elle
# provient d'un seul échantillon, et sous-estimer l'avantage est la seule
# erreur qui ne coûte rien.
MAX_EDGE = 0.12

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
# Deux mesures du backtest se contredisent en apparence, et il faut les
# distinguer :
#
# - le *biais de calibration* (écart entre probabilité prédite et fréquence
#   observée) croît avec la distance, ce qui suggère un horizon court ;
# - l'*espérance réalisée* est maximale pour des objectifs situés entre 3.5 et
#   5.3 ATR (+0.28 et +0.23 R), et négative en deçà comme au-delà.
#
# La première mesure porte sur la justesse de la prédiction, la seconde sur la
# rentabilité : ce sont deux choses différentes. C'est la seconde qui doit
# guider ce réglage, puisqu'il sert à choisir un objectif.
#
# La sonde de pouvoir prédictif tranche plus nettement que le backtest : le
# coefficient d'information du score croît de façon monotone avec l'horizon,
# de +0.022 à 6 bougies jusqu'à +0.063 à 48, sans plafonner. L'avantage ne
# s'épuise donc pas à court terme — il se déploie au contraire lentement.
#
# L'amplitude cumulée d'un marché croît en racine du temps : 48 bougies
# correspondent à environ 7 ATR de parcours. C'est la valeur retenue, et elle
# constitue une borne basse puisque la mesure ne montre aucun essoufflement à
# cet horizon.
EDGE_HORIZON_ATR = 7.0

# Le modèle de ruine du joueur est invariant d'échelle : à ratio
# rendement/risque égal, il juge un stop à 1 ATR aussi sûr qu'un stop à 3 ATR.
# C'est faux dans les deux sens, et les trois corrections ci-dessous rétablissent
# ce que la structure apporte réellement.

# 1. Un stop adossé à un niveau réellement respecté. Mesuré sur 679 trades non
#    filtrés : écart au seuil de rentabilité de +1.1 point sans niveau, +1.9
#    avec un niveau faible, +2.9 avec un niveau moyen, puis -3.4 pour les
#    niveaux les plus solides (sur 45 trades seulement, donc peu concluant).
#    L'effet existe mais reste modeste et non monotone : la valeur de 0.02,
#    déjà prudente, est conservée.
STRUCTURE_EDGE = 0.02

# 2. Un stop trop serré est touché par le bruit ordinaire de la bougie.
#    L'effet est réel — les stops de 1.2 à 1.6 ATR ressortent 2.8 points sous
#    leur seuil de rentabilité, contre +4.5 points pour la tranche 1.6-2.2 —
#    mais son ampleur était très surestimée. La pénalité valait 0.22, soit
#    sept fois l'écart mesuré ; elle est ramenée à 0.05, ce qui reproduit
#    environ 3 points au maximum de son application.
NOISE_FLOOR_ATR = 1.6
NOISE_PENALTY = 0.05

# 3. Un objectif exigeant de franchir une résistance. **Mesuré : aucun effet.**
#    Le taux de réussite est plat selon l'obstacle (44.8 %, 39.6 %, 48.3 %,
#    46.2 % du plus dégagé au plus encombré) et n'est même pas monotone, la
#    tranche « moyen » faisant mieux que la tranche « aucun ». Pendant ce
#    temps le modèle prédisait un effondrement de 45.7 % à 30.1 %.
#
#    Cette pénalité supposée, qui retranchait jusqu'à 0.195 de probabilité —
#    plus que l'avantage maximal mesuré, lui, à 0.12 — était la principale
#    cause du blocage des signaux. Elle est annulée.
#
#    L'obstacle reste calculé et exposé : il conserve une valeur descriptive
#    pour le lecteur d'un plan, et permettra de refaire la mesure sur un
#    échantillon plus large. Il n'entre simplement plus dans la décision.
OBSTACLE_PENALTY = 0.0

# 4. Malédiction du vainqueur, et pourquoi il ne faut PAS corriger la
#    probabilité pour elle.
#
#    Sur l'échantillon complet, non filtré, le modèle est légèrement
#    sous-confiant : il annonce 38 à 42 % de réussite pour 45 à 48 % observés.
#    Sur les seuls trades retenus par le filtre d'espérance, il devient
#    surconfiant : 27.9 % annoncés pour 21.7 % observés.
#
#    Ces deux constats ne se contredisent pas, ils décrivent un biais de
#    sélection. Retenir les espérances les plus élevées revient à retenir les
#    estimations les plus chanceuses, et le réalisé retombe vers sa moyenne.
#    C'est la malédiction du vainqueur, et elle frappe tout système qui
#    sélectionne sur une grandeur estimée.
#
#    Un décalage appliqué à la probabilité corrigerait le second constat en
#    cassant le premier. La bonne réponse est d'exiger une marge à la
#    sélection — voir `MIN_EXPECTED_R` dans `strategy.py` — plutôt que de
#    déformer un modèle qui décrit correctement la population.
CALIBRATION_OFFSET = 0.0


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
    # Entrées des pénalités, conservées pour pouvoir les confronter aux faits :
    # elles sont supposées, contrairement à l'avantage qui est mesuré.
    stop_strength: float = 0.0
    obstacle: float = 0.0

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

    p = (p_base + edge + structure_bonus
         - noise_penalty - obstacle_penalty - CALIBRATION_OFFSET)
    return float(np.clip(p, 0.02, 0.98))


def cost_in_r(price: float, stop_dist: float, klass: str) -> float:
    """Coût de l'aller-retour, exprimé en multiples de risque.

    Le point aveugle du modèle initial. L'espérance était calculée comme si
    entrer et sortir était gratuit ; le backtest a montré qu'un stop touché
    coûte en réalité -1.046 R et non -1.000 R, et qu'un objectif atteint
    rapporte moins que son ratio nominal.

    Le coût est d'autant plus lourd que le stop est serré : à frais constants,
    un stop à 0.5 % du prix les supporte quatre fois plus mal qu'un stop à
    2 %. C'est ce qui rend les stops très serrés perdants même quand la
    probabilité de les éviter semble bonne.
    """
    from .paper import _cost_bps

    if stop_dist <= 0 or price <= 0:
        return 0.0
    return (_cost_bps(klass) / 10_000.0) * price / stop_dist


def expected_r(win_prob: float, rr: float, cost_r: float = 0.0) -> float:
    """Espérance du trade en multiples de risque, coûts déduits.

    Convention : une perte coûte 1 R plus les frais, un gain rapporte `rr` R
    moins les frais. Les frais étant payés dans les deux cas, ils se
    retranchent simplement de l'espérance.
    """
    return win_prob * rr - (1.0 - win_prob) - cost_r


# --------------------------------------------------------------------------
# Optimisation du plan
# --------------------------------------------------------------------------
def optimal_plan(df: pd.DataFrame, direction: str, score: float,
                 *, regime_quality: float = 0.5,
                 fallback_atr_mult: float = 2.0,
                 fallback_rr: float = 2.0,
                 klass: str = "crypto") -> Plan:
    """Construit le plan de trade maximisant l'espérance mathématique.

    La recherche est contrainte par la structure : les stops candidats sont
    adossés à des niveaux réellement respectés par le marché, et les
    objectifs sont bornés par les obstacles qui se trouvent devant. On ne
    choisit pas un R/R en l'air pour ensuite le plaquer sur le graphique.
    """
    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    if not np.isfinite(atr_v) or atr_v <= 0 or price <= 0 or direction == "neutre":
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult,
                              fallback_rr, klass)

    structure = build_structure(df)
    # Pour un achat, le stop se place sous un support et l'objectif sous une
    # résistance ; pour une vente, tout est inversé.
    stop_side = structure["supports"] if direction == "long" else structure["resistances"]
    target_side = structure["resistances"] if direction == "long" else structure["supports"]

    stop_candidates = _stop_candidates(price, atr_v, direction, stop_side)
    if not stop_candidates:
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult,
                              fallback_rr, klass)

    evaluated: list[Plan] = []
    for stop, stop_dist, stop_basis, stop_strength in stop_candidates:
        for target, rr, target_basis, obstacle in _target_candidates(
                price, atr_v, direction, target_side, stop_dist):
            p = win_probability(stop_dist, abs(target - price), score,
                                regime_quality, atr_v,
                                stop_strength=stop_strength, obstacle=obstacle)
            ev = expected_r(p, rr, cost_in_r(price, stop_dist, klass))
            evaluated.append(Plan(
                entry=round(price, 8), stop=round(stop, 8), target=round(target, 8),
                rr=round(rr, 2), stop_atr=round(stop_dist / atr_v, 2),
                win_prob=round(p, 3), expected_r=round(ev, 3),
                stop_basis=stop_basis, target_basis=target_basis, alternatives=[],
                stop_strength=round(stop_strength, 3), obstacle=round(obstacle, 3)))

    if not evaluated:
        return _fallback_plan(price, atr_v, direction, fallback_atr_mult,
                              fallback_rr, klass)

    # Plusieurs stops candidats peuvent produire des couples (stop, objectif)
    # identiques : on déduplique avant de classer, sinon la liste
    # d'alternatives n'affiche que des doublons du plan retenu.
    unique: dict[tuple[float, float], Plan] = {}
    for pl in evaluated:
        key = (round(pl.stop, 8), round(pl.target, 8))
        if key not in unique or pl.expected_r > unique[key].expected_r:
            unique[key] = pl

    # Départage. Trier sur la seule espérance suffit tant que celle-ci
    # discrimine ; elle cesse de le faire dès que l'avantage tombe à zéro —
    # ce qui arrive pour tout score inférieur à 50 — car l'espérance se réduit
    # alors à `-coût`, identique pour tous les objectifs d'un même stop.
    # Les candidats sont à égalité et l'ordre d'insertion tranche, ce qui
    # collait le R/R contre sa borne basse sur l'ensemble de l'univers.
    #
    # On départage donc les quasi-égalités par la qualité structurelle : un
    # objectif adossé à un niveau réel plutôt qu'à un simple multiple de
    # risque, et à défaut un R/R proche de la zone où l'espérance mesurée est
    # la meilleure (3.5 à 5.3 ATR de distance totale, soit environ 2 R).
    def rang(pl: "Plan") -> tuple:
        structurel = 0 if pl.target_basis.startswith("multiple de risque") else 1
        return (round(pl.expected_r, 3), structurel, -abs(pl.rr - 2.0))

    evaluated = sorted(unique.values(), key=rang, reverse=True)
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


def fixed_plan(df: pd.DataFrame, direction: str, score: float, *,
               atr_mult: float = 2.0, rr: float = 2.0,
               klass: str = "crypto", regime_quality: float = 0.5) -> Plan:
    """Plan fixe, sans aucune optimisation : stop à N ATR, objectif à N R.

    Sert de point de comparaison à `optimal_plan`. La mesure a montré que
    l'espérance estimée ne classe pas les trades — la tranche la mieux notée
    réalisait le deuxième pire résultat. Si un plan qui ignore entièrement la
    structure fait aussi bien, alors l'optimiseur est de la complexité sans
    valeur, et le supprimer vaut mieux que le conserver.

    La probabilité et l'espérance restent calculées avec le même modèle, pour
    que les deux approches soient comparables sur les mêmes grandeurs.
    """
    price = S._last(df["close"])
    atr_v = S._last(I.atr(df["high"], df["low"], df["close"]))
    if direction == "neutre" or not np.isfinite(atr_v) or atr_v <= 0 or price <= 0:
        return Plan(round(price, 8) if np.isfinite(price) else 0.0,
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "aucun", "aucun", [])

    dist = atr_mult * atr_v
    sign = -1.0 if direction == "long" else 1.0
    stop = price + sign * dist
    target = price - sign * dist * rr

    p = win_probability(dist, dist * rr, score, regime_quality, atr_v)
    ev = expected_r(p, rr, cost_in_r(price, dist, klass))
    return Plan(
        entry=round(price, 8), stop=round(max(stop, 0.0), 8),
        target=round(max(target, 0.0), 8), rr=round(rr, 2),
        stop_atr=round(atr_mult, 2), win_prob=round(p, 3),
        expected_r=round(ev, 3),
        stop_basis=f"plan fixe ({atr_mult:.1f} ATR)",
        target_basis=f"plan fixe ({rr:.1f} R)", alternatives=[])


def _fallback_plan(price: float, atr_v: float, direction: str,
                   atr_mult: float, rr_target: float,
                   klass: str = "crypto") -> Plan:
    """Plan de repli en pure volatilité, quand la structure est inexploitable."""
    if direction == "neutre" or not np.isfinite(atr_v) or atr_v <= 0 or price <= 0:
        return Plan(round(price, 8), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    "aucun", "aucun", [])
    dist = atr_mult * atr_v
    sign = -1.0 if direction == "long" else 1.0
    stop = price + sign * dist
    target = price - sign * dist * rr_target
    p = win_probability(dist, dist * rr_target, 50.0)
    ev = expected_r(p, rr_target, cost_in_r(price, dist, klass))
    return Plan(round(price, 8), round(stop, 8), round(target, 8), round(rr_target, 2),
                round(atr_mult, 2), round(p, 3), round(ev, 3),
                f"volatilité pure ({atr_mult:.1f} ATR)",
                f"multiple de risque ({rr_target:.1f} R)", [])
