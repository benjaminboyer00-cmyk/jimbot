"""Dimensionnement de position et contrôle du risque de portefeuille.

Le dimensionnement est la seule partie de la chaîne qui décide combien on
perd quand on a tort — c'est-à-dire la seule qui détermine la survie. Elle
est donc volontairement conservatrice et plafonnée à plusieurs niveaux.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from .config import RISK, risk_mult

log = logging.getLogger("jimbot.risk")

# Plafonds durs, non contournables par le scoring.
MAX_PORTFOLIO_RISK = 0.06      # 6 % du capital risqués simultanément, tous trades confondus
MAX_CORRELATED_RISK = 0.035    # risque cumulé d'un groupe d'actifs corrélés > 0.7
# L'exposition maximale par position est définie par classe d'actif dans
# `config.RISK` (`max_notional_pct`) : elle protège du risque de gap, qui est
# proportionnel à la volatilité de la classe.
CORRELATION_THRESHOLD = 0.7


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Fraction de Kelly, renvoyée en demi-Kelly et plafonnée.

    Kelly plein maximise la croissance asymptotique mais produit des
    drawdowns intolérables et suppose que win_rate et payoff sont connus
    exactement — ce qui est faux. On applique donc systématiquement un
    demi-Kelly, plafonné à 25 %.
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    b = avg_win / avg_loss           # ratio gain/perte
    q = 1.0 - win_rate
    f = (b * win_rate - q) / b       # formule de Kelly
    return float(np.clip(f * 0.5, 0.0, 0.25))


def position_size(capital: float, entry: float, stop: float, klass: str,
                  *, score: float = 60.0, kelly: float | None = None) -> dict:
    """Taille de position par risque fractionnel fixe, modulée par la confiance.

    Le raisonnement est inversé par rapport à l'intuition : on ne décide pas
    « j'achète pour 1000 € », on décide « je perds au maximum 1 % si le stop
    saute », et la taille en découle mécaniquement de la distance au stop.
    """
    profile = RISK.get(klass, RISK["crypto"])
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or entry <= 0 or capital <= 0:
        return {"units": 0.0, "notional": 0.0, "risk_amount": 0.0,
                "risk_pct": 0.0, "reason": "stop invalide ou capital nul"}

    # Un score de 60 (seuil) engage la moitié du risque nominal, un score de
    # 100 l'engage entièrement : la conviction module l'exposition.
    conviction = float(np.clip((score - 50.0) / 50.0, 0.1, 1.0))
    # `RISK_MULT` permet à un petit compte de produire des tailles que le
    # courtier accepte. Il ne change pas la qualité du signal, seulement la
    # vitesse à laquelle on encaisse ce qu'il vaut — dans les deux sens.
    risk_pct = profile.risk_pct * conviction * risk_mult()
    if kelly is not None and kelly > 0:
        # Le Kelly issu de l'historique réel plafonne le risque nominal,
        # il ne l'augmente jamais.
        risk_pct = min(risk_pct, kelly)

    risk_amount = capital * risk_pct
    units = risk_amount / risk_per_unit
    notional = units * entry

    reason = f"risque {risk_pct * 100:.2f} % du capital, stop à {risk_per_unit / entry * 100:.2f} %"
    cap_notional = capital * profile.max_notional_pct
    if notional > cap_notional:
        # Un stop très serré produit une taille énorme : on plafonne le
        # notionnel, quitte à risquer moins que prévu.
        units = cap_notional / entry
        notional = cap_notional
        risk_amount = units * risk_per_unit
        reason += f" · plafonné à {profile.max_notional_pct * 100:.0f} % du capital"

    return {
        "units": round(units, 8),
        "notional": round(notional, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_pct": round(risk_amount / capital * 100, 3),
        "reason": reason,
    }


def portfolio_gate(open_positions: list[dict], candidate: dict, capital: float,
                   corr_matrix=None) -> tuple[bool, str]:
    """Autorise ou refuse une nouvelle position au niveau du portefeuille.

    Le scoring décide *quoi* acheter ; cette fonction décide si on a encore
    le droit d'acheter quoi que ce soit. Elle a le dernier mot.
    """
    klass = candidate["klass"]
    profile = RISK.get(klass, RISK["crypto"])

    same_class = [p for p in open_positions if p.get("klass") == klass]
    if len(same_class) >= profile.max_positions:
        return False, f"déjà {len(same_class)} positions ouvertes en {klass} (max {profile.max_positions})"

    if any(p.get("symbol") == candidate["symbol"] for p in open_positions):
        return False, f"position déjà ouverte sur {candidate['symbol']}"

    current_risk = sum(float(p.get("risk_amount", 0.0)) for p in open_positions)
    new_risk = float(candidate.get("risk_amount", 0.0))
    total_pct = (current_risk + new_risk) / capital if capital > 0 else 1.0
    if total_pct > MAX_PORTFOLIO_RISK:
        return False, (f"risque portefeuille {total_pct * 100:.1f} % > "
                       f"{MAX_PORTFOLIO_RISK * 100:.0f} % autorisés")

    # Trois longs sur BTC, ETH et SOL corrélés à 0.9 ne sont pas trois paris
    # mais un seul, de taille triple. On agrège le risque des actifs corrélés.
    if corr_matrix is not None and not corr_matrix.empty:
        sym = candidate["symbol"]
        if sym in corr_matrix.columns:
            correlated_risk = new_risk
            names = []
            for p in open_positions:
                other = p.get("symbol")
                if other not in corr_matrix.columns:
                    continue
                c = corr_matrix.loc[sym, other]
                if not np.isfinite(c) or abs(c) < CORRELATION_THRESHOLD:
                    continue
                # Une corrélation négative entre positions de sens opposé est
                # en réalité une corrélation positive de risque.
                same_side = p.get("direction") == candidate.get("direction")
                if (c > 0) == same_side:
                    correlated_risk += float(p.get("risk_amount", 0.0))
                    names.append(f"{other} ({c:+.2f})")
            if names and correlated_risk / capital > MAX_CORRELATED_RISK:
                return False, (f"risque corrélé {correlated_risk / capital * 100:.1f} % avec "
                               f"{', '.join(names)} > {MAX_CORRELATED_RISK * 100:.1f} %")

    return True, "validé"


def trailing_stop(entry: float, current: float, stop: float, atr: float,
                  direction: str, *, activate_at_r: float = 1.0,
                  trail_atr: float = 1.5) -> tuple[float, str]:
    """Remonte le stop une fois le trade en profit d'au moins 1 R.

    Ne descend jamais : un stop suiveur qui recule n'est pas un stop.
    """
    risk = abs(entry - stop)
    if risk <= 0 or atr <= 0:
        return stop, "inchangé"

    if direction == "long":
        progress = (current - entry) / risk
        if progress < activate_at_r:
            return stop, "inchangé"
        new_stop = max(stop, entry, current - trail_atr * atr)
        moved = new_stop > stop
    else:
        progress = (entry - current) / risk
        if progress < activate_at_r:
            return stop, "inchangé"
        new_stop = min(stop, entry, current + trail_atr * atr)
        moved = new_stop < stop

    if not moved:
        return stop, "inchangé"
    label = "sécurisé au point mort" if math.isclose(new_stop, entry, rel_tol=1e-9) else \
            f"suiveur à {trail_atr} ATR ({progress:.1f} R de profit)"
    return round(new_stop, 8), label
