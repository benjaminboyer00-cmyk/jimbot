"""Portefeuille papier : exécution simulée, suivi et statistiques de performance.

L'objectif n'est pas de produire une jolie courbe mais de mesurer honnêtement
si le moteur a un edge. Deux conséquences dans le code :

1. Les frais et le glissement sont toujours appliqués. Un backtest sans coûts
   transforme n'importe quelle stratégie en machine à gagner.
2. Les sorties sont détectées sur le plus-haut/plus-bas des bougies écoulées,
   pas sur le seul dernier prix. Sinon une mèche qui touche le stop puis se
   retourne serait ignorée, ce qui gonfle artificiellement les résultats.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

from . import stats as S
from . import risk as R
from .config import SETTINGS
from .store import now_iso

log = logging.getLogger("jimbot.paper")

# Coûts d'exécution appliqués à l'entrée ET à la sortie.
FEE_BPS = {"crypto": 6.0, "meme": 35.0, "forex": 1.5, "index": 2.0}
SLIPPAGE_BPS = {"crypto": 4.0, "meme": 90.0, "forex": 1.0, "index": 2.0}

# Une position qui n'a ni touché son stop ni sa cible finit par être fermée :
# le signal qui l'a ouverte n'est plus valide.
MAX_HOLD_BARS = 120


@dataclass
class Position:
    """Une position ouverte dans le portefeuille papier."""

    symbol: str
    label: str
    klass: str
    direction: str
    entry: float          # prix d'entrée effectif, coûts inclus
    entry_ref: float      # prix de marché au moment du signal
    stop: float
    target: float
    units: float
    notional: float
    risk_amount: float
    opened_at: str
    score: float
    regime: str
    bars_held: int = 0
    stop_note: str = "initial"
    mfe: float = 0.0      # maximum favorable excursion, en R
    mae: float = 0.0      # maximum adverse excursion, en R

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def unrealized(self, price: float) -> float:
        sign = 1.0 if self.direction == "long" else -1.0
        return sign * (price - self.entry) * self.units

    def r_multiple(self, price: float) -> float:
        """Profit latent exprimé en multiples du risque initial."""
        rpu = self.risk_per_unit
        if rpu <= 0:
            return 0.0
        sign = 1.0 if self.direction == "long" else -1.0
        return sign * (price - self.entry) / rpu


@dataclass
class Trade:
    """Une position fermée, avec son résultat définitif."""

    symbol: str
    label: str
    klass: str
    direction: str
    entry: float
    exit: float
    units: float
    pnl: float
    pnl_pct: float
    r_multiple: float
    fees: float
    opened_at: str
    closed_at: str
    bars_held: int
    reason: str           # "stop" | "cible" | "expiration" | "inversion"
    score: float
    regime: str
    mfe: float = 0.0
    mae: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# Coût réel par instrument, en points de base d'aller-retour, mesuré sur les
# cotations du courtier plutôt que déduit de la classe d'actif.
#
# Le coût par classe suppose que tous les membres d'une classe coûtent la même
# chose. Mesuré chez Capital.com le 5 septembre 2026, c'est faux d'un facteur
# cinq à l'intérieur même de la crypto :
#
#     BTCUSD    6,3 pb     ETHUSD    7,1 pb     <- conformes à l'hypothèse
#     XRPUSD   50,0 pb     DOGEUSD  50,0 pb     <- cinq fois plus
#
# L'écart n'est pas cosmétique. Le coût en R vaut `pb/10000 × prix / distance
# au stop` : sur un stop à 3 % du prix, 50 pb coûtent 0,167 R contre 0,033 R
# pour 10 pb. Un plan estimé à peine positif devient franchement perdant, et le
# filtre d'espérance minimale le laissait passer.
#
# Ces valeurs sont des mesures, pas des estimations, et elles se périment : un
# spread se resserre et s'élargit selon l'heure et la liquidité. Elles sont
# volontairement prises au plus large observé.
COUT_PAR_SYMBOLE: dict[str, float] = {
    "XRP-USD": 55.0,
    "DOGE-USD": 55.0,
}


def _cost_bps(klass: str, symbol: str | None = None) -> float:
    """Coût d'un aller-retour, en points de base.

    Le coût propre à l'instrument l'emporte sur celui de sa classe : à
    l'intérieur d'une même classe, les spreads varient d'un facteur cinq.
    """
    if symbol and symbol in COUT_PAR_SYMBOLE:
        return COUT_PAR_SYMBOLE[symbol]
    return FEE_BPS.get(klass, 10.0) + SLIPPAGE_BPS.get(klass, 5.0)


def apply_costs(price: float, klass: str, direction: str, opening: bool,
                symbol: str | None = None) -> float:
    """Dégrade le prix dans le sens défavorable au trader.

    À l'ouverture d'un long on paie plus cher, à la fermeture on vend moins
    cher — et symétriquement pour un short.
    """
    bps = _cost_bps(klass, symbol) / 2.0  # la moitié des coûts par jambe
    adverse = 1.0 if (direction == "long") == opening else -1.0
    return price * (1.0 + adverse * bps / 10_000.0)


class Portfolio:
    """État du portefeuille papier, sérialisable en JSON."""

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.capital: float = float(state.get("capital", SETTINGS.paper_capital))
        self.initial: float = float(state.get("initial", SETTINGS.paper_capital))
        self.positions: list[Position] = [Position(**p) for p in state.get("positions", [])]
        self.equity_curve: list[dict] = state.get("equity_curve", [])
        self.closed_count: int = int(state.get("closed_count", 0))

    # -- sérialisation ------------------------------------------------------
    def to_dict(self, marks: dict[str, float] | None = None) -> dict:
        marks = marks or {}
        equity = self.equity(marks)
        return {
            "capital": round(self.capital, 2),
            "initial": round(self.initial, 2),
            "equity": round(equity, 2),
            "open_risk": round(sum(p.risk_amount for p in self.positions), 2),
            "positions": [p.to_dict() for p in self.positions],
            "equity_curve": self.equity_curve[-500:],
            "closed_count": self.closed_count,
            "updated_at": now_iso(),
        }

    def equity(self, marks: dict[str, float]) -> float:
        """Capital + valeur latente des positions ouvertes."""
        latent = sum(p.unrealized(marks[p.symbol])
                     for p in self.positions if p.symbol in marks)
        return self.capital + latent

    # -- cycle de vie des positions ----------------------------------------
    def open(self, signal, sizing: dict) -> Position | None:
        """Ouvre une position à partir d'un signal validé."""
        if sizing["units"] <= 0:
            return None
        entry = apply_costs(signal.price, signal.klass, signal.direction, opening=True)
        pos = Position(
            symbol=signal.symbol, label=signal.label, klass=signal.klass,
            direction=signal.direction, entry=round(entry, 8),
            entry_ref=signal.price, stop=signal.stop, target=signal.target,
            units=sizing["units"], notional=sizing["notional"],
            risk_amount=sizing["risk_amount"], opened_at=now_iso(),
            score=signal.score, regime=signal.regime["name"],
        )
        self.positions.append(pos)
        log.info("ouverture %s %s à %.6f (stop %.6f, cible %.6f)",
                 pos.direction, pos.symbol, pos.entry, pos.stop, pos.target)
        return pos

    def update(self, symbol: str, candles: pd.DataFrame) -> list[Trade]:
        """Fait vivre les positions d'un actif sur les bougies écoulées.

        Renvoie les trades fermés pendant cette mise à jour.
        """
        closed: list[Trade] = []
        if candles.empty:
            return closed

        for pos in [p for p in self.positions if p.symbol == symbol]:
            new_bars = candles[candles.index > pd.Timestamp(pos.opened_at)]
            # Au minimum, la dernière bougie sert de référence de marché.
            window = new_bars if not new_bars.empty else candles.tail(1)

            # Affectation, et non incrément.
            #
            # `new_bars` contient toutes les bougies depuis l'ouverture, pas
            # celles arrivées depuis la dernière mise à jour. En cumulant, le
            # compteur additionnait un total à chaque scan et croissait comme le
            # carré du temps écoulé : une position ouverte depuis treize heures
            # — treize bougies horaires — affichait 128 bougies détenues et
            # franchissait le plafond de 120.
            #
            # Conséquence : les positions expiraient au bout d'une demi-journée
            # au lieu des cinq jours prévus. Cinq des sept trades clos du
            # portefeuille papier portent la mention « expiration » après 13 à
            # 19 heures, et mesurent donc une stratégie qui n'est pas celle que
            # le site décrit. Le registre de redevabilité, qui rejoue les
            # bougies sans passer par ce compteur, ne partageait pas l'erreur —
            # d'où le même signal DOGE affiché clos à +0,54 R d'un côté et
            # toujours en cours à +0,09 R de l'autre.
            pos.bars_held = len(new_bars)

            hi = float(window["high"].max())
            lo = float(window["low"].min())
            last = float(window["close"].iloc[-1])

            # Excursions extrêmes, mesurées sur les mèches.
            best = hi if pos.direction == "long" else lo
            worst = lo if pos.direction == "long" else hi
            pos.mfe = round(max(pos.mfe, pos.r_multiple(best)), 2)
            pos.mae = round(min(pos.mae, pos.r_multiple(worst)), 2)

            exit_price, reason = self._exit_check(pos, hi, lo)
            if exit_price is not None:
                closed.append(self._close(pos, exit_price, reason))
                continue

            if pos.bars_held >= MAX_HOLD_BARS:
                closed.append(self._close(pos, last, "expiration"))
                continue

            # Stop suiveur, calculé sur l'ATR courant.
            from . import indicators as Ind
            atr_v = S._last(Ind.atr(candles["high"], candles["low"], candles["close"]), 0.0)
            new_stop, note = R.trailing_stop(pos.entry, last, pos.stop, atr_v, pos.direction)
            if note != "inchangé":
                pos.stop, pos.stop_note = new_stop, note
                log.info("%s : stop %s -> %.6f", pos.symbol, note, new_stop)

        return closed

    def _exit_check(self, pos: Position, hi: float, lo: float) -> tuple[float | None, str]:
        """Détecte stop et cible sur la fourchette des bougies écoulées.

        Si les deux ont été touchés dans la même fenêtre, on retient le stop :
        sans données intra-bougie on ne peut pas savoir lequel est arrivé en
        premier, et faire l'hypothèse favorable serait de l'auto-illusion.
        """
        if pos.direction == "long":
            hit_stop = pos.stop > 0 and lo <= pos.stop
            hit_target = pos.target > 0 and hi >= pos.target
        else:
            hit_stop = pos.stop > 0 and hi >= pos.stop
            hit_target = pos.target > 0 and lo <= pos.target

        if hit_stop:
            return pos.stop, "stop"
        if hit_target:
            return pos.target, "cible"
        return None, ""

    def close_symbol(self, symbol: str, price: float, reason: str) -> list[Trade]:
        """Ferme manuellement toutes les positions d'un actif (ex. inversion de signal)."""
        return [self._close(p, price, reason)
                for p in [x for x in self.positions if x.symbol == symbol]]

    def _close(self, pos: Position, raw_exit: float, reason: str) -> Trade:
        exit_price = apply_costs(raw_exit, pos.klass, pos.direction, opening=False)
        sign = 1.0 if pos.direction == "long" else -1.0
        pnl = sign * (exit_price - pos.entry) * pos.units
        # Coût total effectivement supporté sur l'aller-retour.
        fees = pos.notional * _cost_bps(pos.klass) / 10_000.0
        rpu = pos.risk_per_unit
        r_mult = (sign * (exit_price - pos.entry) / rpu) if rpu > 0 else 0.0

        self.capital += pnl
        self.closed_count += 1
        self.positions = [p for p in self.positions if p is not pos]

        trade = Trade(
            symbol=pos.symbol, label=pos.label, klass=pos.klass,
            direction=pos.direction, entry=round(pos.entry, 8),
            exit=round(exit_price, 8), units=pos.units, pnl=round(pnl, 2),
            pnl_pct=round(sign * (exit_price / pos.entry - 1.0) * 100, 3),
            r_multiple=round(r_mult, 2), fees=round(fees, 2),
            opened_at=pos.opened_at, closed_at=now_iso(), bars_held=pos.bars_held,
            reason=reason, score=pos.score, regime=pos.regime,
            mfe=pos.mfe, mae=pos.mae,
        )
        log.info("fermeture %s %s : %s, PnL %+.2f (%+.2f R)",
                 pos.direction, pos.symbol, reason, pnl, r_mult)
        return trade

    def mark(self, marks: dict[str, float]) -> None:
        """Ajoute un point à la courbe d'equity."""
        self.equity_curve.append({
            "t": now_iso(),
            "equity": round(self.equity(marks), 2),
            "capital": round(self.capital, 2),
            "open": len(self.positions),
        })


# --------------------------------------------------------------------------
# Statistiques de performance
# --------------------------------------------------------------------------
def performance(trades: list[dict], equity_curve: list[dict],
                initial: float) -> dict:
    """Calcule les statistiques de performance sur les trades fermés.

    Volontairement complet : winrate seul ne dit rien (on peut gagner 90 %
    du temps et perdre de l'argent). Le facteur de profit et l'espérance en R
    sont les deux chiffres qui comptent réellement.
    """
    if not trades:
        return {"trades": 0, "note": "aucun trade fermé pour l'instant"}

    df = pd.DataFrame(trades)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    gross_win = float(wins["pnl"].sum())
    gross_loss = float(abs(losses["pnl"].sum()))

    equity = pd.Series([e["equity"] for e in equity_curve]) if equity_curve else pd.Series(dtype=float)
    rets = equity.pct_change().dropna() if len(equity) > 2 else pd.Series(dtype=float)

    avg_win = float(wins["pnl"].mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses["pnl"].mean())) if len(losses) else 0.0
    win_rate = len(wins) / len(df)

    out = {
        "trades": len(df),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "total_pnl": round(float(df["pnl"].sum()), 2),
        "total_return_pct": round(float(df["pnl"].sum()) / initial * 100, 2) if initial else 0.0,
        # Le facteur de profit : > 1.5 est bon, < 1.0 est une stratégie perdante.
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        # L'espérance en R est la mesure la plus honnête : combien on gagne en
        # moyenne par unité de risque engagée.
        "expectancy_r": round(float(df["r_multiple"].mean()), 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(float(df["pnl"].max()), 2),
        "worst_trade": round(float(df["pnl"].min()), 2),
        "avg_bars_held": round(float(df["bars_held"].mean()), 1),
        "total_fees": round(float(df["fees"].sum()), 2),
        "max_drawdown_pct": round(S.max_drawdown(equity) * 100, 2) if len(equity) > 2 else 0.0,
        "sharpe": round(S.sharpe(rets, periods_per_year=96 * 365) if len(rets) > 3 else 0.0, 2),
        "sortino": round(S.sortino(rets, periods_per_year=96 * 365) if len(rets) > 3 else 0.0, 2),
        "kelly": round(R.kelly_fraction(win_rate, avg_win, avg_loss), 3),
        "by_reason": df.groupby("reason")["pnl"].agg(["count", "sum"]).round(2).to_dict("index"),
        "by_class": df.groupby("klass")["pnl"].agg(["count", "sum"]).round(2).to_dict("index"),
        "by_regime": df.groupby("regime")["r_multiple"].agg(["count", "mean"]).round(2).to_dict("index"),
    }

    # Séries de gains/pertes consécutifs : révèle la dépendance entre trades.
    signs = (df["pnl"] > 0).astype(int).tolist()
    out["max_win_streak"] = _max_streak(signs, 1)
    out["max_loss_streak"] = _max_streak(signs, 0)
    return out


def _max_streak(seq: list[int], value: int) -> int:
    best = cur = 0
    for x in seq:
        cur = cur + 1 if x == value else 0
        best = max(best, cur)
    return best
