"""Graphiques matplotlib, rendus en PNG mémoire pour insertion dans le PDF.

Charte volontairement sobre : gris neutres, une seule couleur d'accent par
sens (vert/rouge désaturés), pas de dégradé, pas d'ombre, pas de fond coloré.
Le graphique doit se lire imprimé en noir et blanc.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")  # aucun serveur graphique dans un runner CI

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import indicators as I

log = logging.getLogger("jimbot.charts")

INK = "#1a1a1a"
MUTED = "#6b7280"
GRID = "#e5e7eb"
UP = "#2e7d5b"
DOWN = "#9b3a3a"
ACCENT = "#3d5a80"
PAPER = "#ffffff"

plt.rcParams.update({
    "figure.facecolor": PAPER, "axes.facecolor": PAPER,
    "font.family": "DejaVu Sans", "font.size": 8,
    "axes.edgecolor": GRID, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "figure.dpi": 150,
})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=PAPER)
    plt.close(fig)
    return buf.getvalue()


def price_chart(df: pd.DataFrame, sig: dict, *, bars: int = 160,
                title: bool = True) -> bytes:
    """Prix, moyennes, bandes, niveaux du trade, volume et RSI."""
    d = df.tail(bars)
    close = d["close"]
    fig, (ax, ax_v, ax_r) = plt.subplots(
        3, 1, figsize=(7.2, 4.6), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 0.7, 1.0], "hspace": 0.12})

    # --- prix ---
    lower, mid, upper, _, _ = I.bollinger(df["close"], 20, 2.0)
    ax.fill_between(d.index, lower.tail(bars), upper.tail(bars),
                    color=ACCENT, alpha=0.06, linewidth=0, label="Bollinger 20")
    ax.plot(d.index, close, color=INK, linewidth=1.1, label="Prix")
    for span, style, lw in ((20, "-", 0.9), (50, "--", 0.9), (200, ":", 1.0)):
        e = I.ema(df["close"], span).tail(bars)
        if e.notna().any():
            ax.plot(d.index, e, color=MUTED, linestyle=style, linewidth=lw,
                    label=f"EMA {span}")

    if sig.get("stop", 0) > 0:
        col = UP if sig["direction"] == "long" else DOWN
        # xmax=0.90 : les lignes s'arrêtent avant la marge des libellés,
        # sinon le trait barre le texte.
        ax.axhline(sig["entry"], color=col, linewidth=1.0, alpha=0.9, xmax=0.90)
        ax.axhline(sig["stop"], color=DOWN, linewidth=0.9, linestyle="--",
                   alpha=0.8, xmax=0.90)
        ax.axhline(sig["target"], color=UP, linewidth=0.9, linestyle="--",
                   alpha=0.8, xmax=0.90)
        x = d.index[-1]
        for y, txt, c in ((sig["entry"], "entrée", col),
                          (sig["stop"], "stop", DOWN),
                          (sig["target"], "objectif", UP)):
            ax.annotate(txt, xy=(x, y), xytext=(4, 0), textcoords="offset points",
                        color=c, fontsize=7, va="center")

    ax.set_ylabel("Prix")
    # Marge à droite pour les libellés de niveaux, sinon ils sont rognés.
    span = d.index[-1] - d.index[0]
    ax.set_xlim(d.index[0], d.index[-1] + span * 0.10)
    # La légende est sortie au-dessus du graphique : placée dedans, elle
    # recouvrait la ligne d'objectif.
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=5, fontsize=6.5,
              borderaxespad=0)
    if title:
        # Placé au-dessus de la légende, sinon les deux se chevauchent.
        ax.set_title(f"{sig['label']} ({sig['symbol']}) — {sig['timeframe']}",
                     loc="left", fontsize=9.5, color=INK, pad=20)

    # --- volume ---
    if d["volume"].sum() > 0:
        up_bars = d["close"] >= d["open"]
        ax_v.bar(d.index[up_bars], d["volume"][up_bars], color=UP, alpha=0.45,
                 width=_width(d))
        ax_v.bar(d.index[~up_bars], d["volume"][~up_bars], color=DOWN, alpha=0.45,
                 width=_width(d))
        ax_v.set_ylabel("Vol.", fontsize=7)
    else:
        ax_v.text(0.5, 0.5, "volume non disponible", transform=ax_v.transAxes,
                  ha="center", va="center", color=MUTED, fontsize=7)
    ax_v.set_yticks([])

    # --- RSI ---
    r = I.rsi(df["close"]).tail(bars)
    ax_r.plot(d.index, r, color=ACCENT, linewidth=1.0)
    ax_r.axhline(70, color=MUTED, linewidth=0.6, linestyle="--")
    ax_r.axhline(30, color=MUTED, linewidth=0.6, linestyle="--")
    ax_r.fill_between(d.index, 30, 70, color=MUTED, alpha=0.05, linewidth=0)
    ax_r.set_ylim(0, 100)
    ax_r.set_yticks([30, 50, 70])
    ax_r.set_ylabel("RSI", fontsize=7)

    ax_r.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate(rotation=0, ha="center")
    return _png(fig)


def factor_chart(sig: dict) -> bytes:
    """Contribution de chaque facteur au score, en barres horizontales."""
    factors = sig["factors"]
    names = [f["name"].replace("_", " ") for f in factors]
    vals = [f["contribution"] * 100 for f in factors]
    colors = [UP if v > 0 else DOWN if v < 0 else MUTED for v in vals]

    fig, ax = plt.subplots(figsize=(7.2, 0.42 * len(factors) + 0.7))
    ax.barh(names, vals, color=colors, alpha=0.85, height=0.6)
    ax.axvline(0, color=INK, linewidth=0.8)
    for i, v in enumerate(vals):
        ax.text(v + (0.6 if v >= 0 else -0.6), i, f"{v:+.1f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=7, color=INK)
    ax.set_xlabel("Contribution au score (points)")
    ax.grid(axis="y", visible=False)
    lim = max(abs(min(vals)), abs(max(vals)), 1) * 1.35
    ax.set_xlim(-lim, lim)
    ax.invert_yaxis()
    return _png(fig)


def equity_chart(curve: list[dict], initial: float) -> bytes:
    """Courbe d'equity et drawdown sous-jacent."""
    if len(curve) < 2:
        fig, ax = plt.subplots(figsize=(7.2, 2.4))
        ax.text(0.5, 0.5, "historique insuffisant pour tracer la courbe",
                transform=ax.transAxes, ha="center", va="center", color=MUTED)
        ax.set_axis_off()
        return _png(fig)

    eq = pd.Series([c["equity"] for c in curve])
    t = pd.to_datetime([c["t"] for c in curve], utc=True, format="ISO8601")
    peak = eq.cummax()
    dd = (eq / peak - 1.0) * 100

    fig, (ax, ax_d) = plt.subplots(2, 1, figsize=(7.2, 3.2), sharex=True,
                                   gridspec_kw={"height_ratios": [2.4, 1.0], "hspace": 0.15})
    ax.plot(t, eq, color=ACCENT, linewidth=1.2)
    ax.axhline(initial, color=MUTED, linewidth=0.8, linestyle="--")
    ax.fill_between(t, initial, eq, where=(eq >= initial), color=UP, alpha=0.12, linewidth=0)
    ax.fill_between(t, initial, eq, where=(eq < initial), color=DOWN, alpha=0.12, linewidth=0)
    ax.set_ylabel("Capital")
    ax.set_title("Portefeuille papier", loc="left", fontsize=9.5, pad=6)

    ax_d.fill_between(t, dd, 0, color=DOWN, alpha=0.28, linewidth=0)
    ax_d.plot(t, dd, color=DOWN, linewidth=0.8)
    ax_d.set_ylabel("Drawdown %", fontsize=7)
    ax_d.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate(rotation=0, ha="center")
    return _png(fig)


def score_overview(signals: list[dict]) -> bytes:
    """Vue d'ensemble : score signé de chaque actif suivi."""
    if not signals:
        fig, ax = plt.subplots(figsize=(7.2, 1.2))
        ax.text(0.5, 0.5, "aucun actif analysé", transform=ax.transAxes,
                ha="center", va="center", color=MUTED)
        ax.set_axis_off()
        return _png(fig)

    ordered = sorted(signals, key=lambda s: _signed(s))
    names = [s["symbol"] for s in ordered]
    vals = [_signed(s) for s in ordered]
    colors = [UP if v > 0 else DOWN for v in vals]

    fig, ax = plt.subplots(figsize=(7.2, 0.26 * len(names) + 0.9))
    ax.barh(names, vals, color=colors, alpha=0.8, height=0.62)
    ax.axvline(0, color=INK, linewidth=0.8)
    for thresh in (-58, 58):
        ax.axvline(thresh, color=MUTED, linewidth=0.7, linestyle=":")
    ax.set_xlim(-100, 100)
    ax.set_xlabel("← vente     score de conviction     achat →")
    ax.grid(axis="y", visible=False)
    ax.set_title("Univers suivi", loc="left", fontsize=9.5, pad=6)
    return _png(fig)


def correlation_heatmap(corr: pd.DataFrame) -> bytes:
    """Matrice de corrélation des rendements."""
    fig, ax = plt.subplots(figsize=(min(7.2, 0.5 * len(corr) + 1.6),
                                    min(6.4, 0.45 * len(corr) + 1.4)))
    data = corr.to_numpy()
    im = ax.imshow(data, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr)), corr.columns, rotation=90, fontsize=6.5)
    ax.set_yticks(range(len(corr)), corr.index, fontsize=6.5)
    for i in range(len(corr)):
        for j in range(len(corr)):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=5.5,
                        color="white" if abs(v) > 0.6 else INK)
    ax.grid(visible=False)
    fig.colorbar(im, ax=ax, shrink=0.7, label="corrélation")
    ax.set_title("Corrélation des rendements", loc="left", fontsize=9.5, pad=6)
    return _png(fig)


def r_distribution(trades: list[dict]) -> bytes:
    """Distribution des résultats en R — la forme compte plus que la moyenne."""
    if len(trades) < 5:
        fig, ax = plt.subplots(figsize=(7.2, 1.2))
        ax.text(0.5, 0.5, f"{len(trades)} trade(s) fermé(s) : trop peu pour une distribution",
                transform=ax.transAxes, ha="center", va="center", color=MUTED)
        ax.set_axis_off()
        return _png(fig)

    rs = [t["r_multiple"] for t in trades]
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    bins = np.linspace(min(rs + [-2.0]), max(rs + [2.0]), 22)
    ax.hist([r for r in rs if r > 0], bins=bins, color=UP, alpha=0.75, label="gagnants")
    ax.hist([r for r in rs if r <= 0], bins=bins, color=DOWN, alpha=0.75, label="perdants")
    mean_r = float(np.mean(rs))
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.axvline(mean_r, color=ACCENT, linewidth=1.1, linestyle="--",
               label=f"espérance {mean_r:+.2f} R")
    ax.set_xlabel("résultat en multiples de risque (R)")
    ax.set_ylabel("nombre de trades")
    ax.legend(fontsize=7)
    ax.set_title("Distribution des résultats", loc="left", fontsize=9.5, pad=6)
    return _png(fig)


def _signed(s: dict) -> float:
    """Score porteur de son sens.

    `score` est une magnitude non signée ; la direction n'est renseignée
    qu'au-delà du seuil. Pour un actif resté neutre, c'est donc le signe de
    `raw_score` qui porte l'orientation — sans lui, une lecture baissière
    faible s'afficherait du côté achat.
    """
    magnitude = s["score"]
    if s["direction"] == "short":
        return -magnitude
    if s["direction"] == "long":
        return magnitude
    return magnitude if s.get("raw_score", 0.0) >= 0 else -magnitude


def _width(d: pd.DataFrame) -> float:
    """Largeur de barre en jours, déduite de l'espacement réel des bougies."""
    if len(d) < 2:
        return 0.02
    delta = (d.index[-1] - d.index[-2]).total_seconds() / 86400.0
    return max(delta * 0.8, 1e-4)
