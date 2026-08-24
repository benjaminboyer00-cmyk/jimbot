"""Génération du rapport PDF quotidien avec ReportLab.

Aucun outil payant : ReportLab (BSD) pour la mise en page, matplotlib pour
les graphiques. La charte est sobre — noir, gris, un vert et un rouge
désaturés — et le document reste lisible imprimé en niveaux de gris.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

from . import charts
from .config import REPORTS_DIR

log = logging.getLogger("jimbot.report")

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d8dbe0")
BAND = colors.HexColor("#f4f5f7")
UP = colors.HexColor("#2e7d5b")
DOWN = colors.HexColor("#9b3a3a")
ACCENT = colors.HexColor("#3d5a80")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=22, leading=26, textColor=INK, alignment=0,
                                spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=10, leading=14, textColor=MUTED, spaceAfter=14),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=INK,
                             spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=10.5, leading=13, textColor=INK,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9, leading=13.5, textColor=INK,
                               alignment=TA_JUSTIFY, spaceAfter=6),
        "small": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica",
                                fontSize=7.5, leading=10, textColor=MUTED, spaceAfter=4),
        "kpi": ParagraphStyle("k", parent=base["Normal"], fontName="Helvetica-Bold",
                              fontSize=15, leading=18, textColor=INK, alignment=1),
        "kpi_label": ParagraphStyle("kl", parent=base["Normal"], fontName="Helvetica",
                                    fontSize=7, leading=9, textColor=MUTED, alignment=1),
    }


class _Doc(BaseDocTemplate):
    """Document A4 avec en-tête discret et pagination."""

    def __init__(self, path: str, subtitle: str, **kw):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN + 6 * mm, bottomMargin=MARGIN, **kw)
        self.subtitle = subtitle
        frame = Frame(MARGIN, MARGIN, PAGE_W - 2 * MARGIN,
                      PAGE_H - 2 * MARGIN - 6 * mm, id="main")
        self.addPageTemplates([PageTemplate(id="std", frames=[frame],
                                            onPage=self._decorate)])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, PAGE_H - MARGIN - 2 * mm, "JIMBOT")
        canvas.setFont("Helvetica", 7.5)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - 2 * mm, self.subtitle)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, PAGE_H - MARGIN - 4 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN - 4 * mm)
        # Pied de page
        canvas.line(MARGIN, MARGIN - 2 * mm, PAGE_W - MARGIN, MARGIN - 2 * mm)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(MARGIN, MARGIN - 5 * mm,
                          "Analyse automatisée à but informatif — ne constitue pas un conseil en investissement.")
        canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 5 * mm, f"page {doc.page}")
        canvas.restoreState()


def _img(png: bytes, width: float = PAGE_W - 2 * MARGIN) -> Image:
    """Insère un PNG en préservant son ratio."""
    from reportlab.lib.utils import ImageReader
    reader = ImageReader(io.BytesIO(png))
    w, h = reader.getSize()
    return Image(io.BytesIO(png), width=width, height=width * h / w)


def _kpi_row(items: list[tuple[str, str, colors.Color | None]], st: dict) -> Table:
    """Bandeau de chiffres clés."""
    cells = [[Paragraph(f'<font color="#{(c or INK).hexval()[2:]}">{v}</font>', st["kpi"])
              for _, v, c in items],
             [Paragraph(k, st["kpi_label"]) for k, _, _ in items]]
    width = (PAGE_W - 2 * MARGIN) / len(items)
    t = Table(cells, colWidths=[width] * len(items), rowHeights=[20, 12])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    return t


def _table(header: list[str], rows: list[list], widths: list[float],
           aligns: dict[int, str] | None = None) -> Table:
    data = [header] + rows
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
    ]
    for col, align in (aligns or {}).items():
        style.append(("ALIGN", (col, 0), (col, -1), align))
    t.setStyle(TableStyle(style))
    return t


def _para(text: str, style) -> list:
    """Convertit un texte à paragraphes en flowables ReportLab."""
    out = []
    for block in (text or "").split("\n\n"):
        block = block.strip()
        if block:
            out.append(Paragraph(_escape(block.replace("\n", " ")), style))
    return out


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _color_for(value: float) -> colors.Color:
    return UP if value > 0 else DOWN if value < 0 else INK


# --------------------------------------------------------------------------
# Rapport principal
# --------------------------------------------------------------------------
def build(*, signals: list[dict], candles: dict, briefing: str,
          narratives: dict[str, str], portfolio: dict, performance: dict,
          trades: list[dict], news: list[dict], memecoins: list[dict],
          corr=None, engine: str = "gabarit",
          meme_report: dict | None = None) -> Path:
    """Assemble le rapport PDF complet et renvoie son chemin."""
    st = _styles()
    now = datetime.now(timezone.utc)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"jimbot-{now:%Y-%m-%d}.pdf"

    doc = _Doc(str(path), subtitle=f"Rapport du {now.day} {MOIS[now.month - 1]} {now.year} · {now:%H:%M} UTC")
    flow: list = []

    # --- Couverture / synthèse ---
    flow.append(Paragraph("Rapport de marché", st["title"]))
    flow.append(Paragraph(
        f"{now.day} {MOIS[now.month - 1]} {now.year} · {len(signals)} actifs "
        f"analysés · rédaction : {engine}", st["subtitle"]))

    actionable = [s for s in signals if s["direction"] != "neutre"]
    equity = portfolio.get("equity", 0.0)
    initial = portfolio.get("initial", 1.0) or 1.0
    ret = (equity / initial - 1.0) * 100
    flow.append(_kpi_row([
        ("Signaux actifs", str(len(actionable)), ACCENT),
        ("Achat", str(sum(1 for s in actionable if s["direction"] == "long")), UP),
        ("Vente", str(sum(1 for s in actionable if s["direction"] == "short")), DOWN),
        ("Capital papier", f"{equity:,.0f}", INK),
        ("Performance", f"{ret:+.2f} %", _color_for(ret)),
        ("Trades fermés", str(performance.get("trades", 0)), INK),
    ], st))
    flow.append(Spacer(1, 10))

    flow.append(Paragraph("Briefing", st["h1"]))
    flow.extend(_para(briefing, st["body"]))

    flow.append(Spacer(1, 6))
    flow.append(_img(charts.score_overview(signals)))

    # --- Signaux détaillés ---
    if actionable:
        flow.append(PageBreak())
        flow.append(Paragraph("Configurations retenues", st["h1"]))
        for sig in sorted(actionable, key=lambda s: -s["score"]):
            flow.extend(_signal_section(sig, candles.get(sig["symbol"]),
                                        narratives.get(sig["symbol"], ""), st))
    else:
        flow.append(Paragraph("Configurations retenues", st["h1"]))
        flow.append(Paragraph(
            "Aucune configuration n'atteint le seuil de conviction requis. "
            "Le moteur reste à l'écart : forcer une position dans un marché "
            "sans structure exploitable est le moyen le plus fiable de perdre "
            "du capital.", st["body"]))

    # --- Univers ---
    flow.append(PageBreak())
    flow.append(Paragraph("Vue d'ensemble de l'univers", st["h1"]))
    rows = []
    for s in sorted(signals, key=lambda x: -x["score"]):
        rows.append([
            s["symbol"], s["label"][:20],
            {"long": "achat", "short": "vente", "neutre": "—"}[s["direction"]],
            f"{s['score']:.0f}", s["regime"]["name"].replace("_", " ")[:18],
            f"{s['atr_pct']:.2f} %",
            f"{s['news_score']:+.2f}" if s["news_count"] else "—",
        ])
    w = PAGE_W - 2 * MARGIN
    flow.append(_table(["Symbole", "Actif", "Sens", "Score", "Régime", "Volatilité", "Presse"],
                       rows, [w * .13, w * .22, w * .11, w * .10, w * .21, w * .12, w * .11],
                       aligns={3: "RIGHT", 5: "RIGHT", 6: "RIGHT"}))

    if corr is not None and not getattr(corr, "empty", True):
        flow.append(Spacer(1, 10))
        flow.append(_img(charts.correlation_heatmap(corr), width=w * 0.82))
        flow.append(Paragraph(
            "Deux positions dans le même sens sur des actifs corrélés au-delà "
            "de 0.7 ne constituent pas deux paris distincts mais un seul, de "
            "taille double. Le moteur en tient compte avant d'ouvrir.", st["small"]))

    # --- Memecoins ---
    meme_report = meme_report or {}
    if memecoins or meme_report.get("screened"):
        flow.append(Paragraph("Memecoins — filtre de survie", st["h2"]))
        flow.append(Paragraph(
            f"{meme_report.get('screened', 0)} jeton(s) en tendance criblés, "
            f"{meme_report.get('retained', len(memecoins))} retenu(s). Le filtre "
            f"impose des exigences croissantes à mesure que le pool est jeune : "
            f"au-delà d'une semaine, 50 000 $ de liquidité suffisent ; entre 8 et "
            f"24 heures, il en faut 150 000 $ et 1,5 M$ de volume. Un cycle sans "
            f"aucun jeton retenu est le cas courant, pas une anomalie.", st["small"]))
    if memecoins:
        rows = [[m["symbol"][:12], m["chain"][:8], f"{m['price_usd']:.8f}".rstrip("0"),
                 f"{m['liquidity_usd']:,.0f}", f"{m['volume_24h']:,.0f}",
                 f"{m['change_24h']:+.1f} %", f"{m['age_hours'] / 24:.0f} j",
                 f"{m['health_score']:.0f}"] for m in memecoins[:12]]
        flow.append(_table(["Jeton", "Chaîne", "Prix $", "Liquidité $", "Volume 24h $",
                            "24h", "Âge", "Robustesse"], rows,
                           [w * .11, w * .10, w * .16, w * .15, w * .16, w * .10, w * .09, w * .13],
                           aligns={2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT",
                                   6: "RIGHT", 7: "RIGHT"}))
        flow.append(Paragraph(
            "Le score de robustesse mesure la profondeur de liquidité, l'activité "
            "et la maturité du pool — c'est-à-dire la capacité à sortir d'une "
            "position. Il ne prédit aucune performance.", st["small"]))

    near = meme_report.get("near_misses") or []
    if near and not memecoins:
        flow.append(Paragraph("Jetons écartés de peu — à surveiller", st["h2"]))
        rows = [[n["symbol"][:12], n["chain"][:8], f"{n['liquidity_usd']:,}",
                 f"{n['volume_24h']:,}", f"{n['age_hours']:.0f} h",
                 _escape(n["reason"])[:52]] for n in near]
        flow.append(_table(["Jeton", "Chaîne", "Liquidité $", "Volume 24h $", "Âge", "Motif"],
                           rows, [w * .11, w * .10, w * .15, w * .16, w * .08, w * .40],
                           aligns={2: "RIGHT", 3: "RIGHT", 4: "RIGHT"}))

    # --- Portefeuille ---
    flow.append(PageBreak())
    flow.append(Paragraph("Portefeuille papier", st["h1"]))
    flow.append(_img(charts.equity_chart(portfolio.get("equity_curve", []), initial)))
    flow.append(Spacer(1, 6))
    flow.extend(_performance_section(performance, portfolio, trades, st, w))

    # --- Actualités ---
    if news:
        flow.append(PageBreak())
        flow.append(Paragraph("Actualités marquantes", st["h1"]))
        flow.append(Paragraph(
            "Sentiment calculé par lexique pondéré : chaque terme reconnu porte "
            "un poids fixe, avec inversion sur les tournures négatives. Le "
            "procédé est reproductible et vérifiable, contrairement à une "
            "notation par modèle de langage.", st["small"]))
        rows = [[n["source"][:12], _escape(n["title"])[:78],
                 f"{n['sentiment']:+.1f}", f"{n['age_hours']:.0f} h",
                 ", ".join(n["assets"][:2]) or "—"] for n in news[:22]]
        flow.append(_table(["Source", "Titre", "Score", "Âge", "Actifs"], rows,
                           [w * .12, w * .58, w * .09, w * .08, w * .13],
                           aligns={2: "RIGHT", 3: "RIGHT"}))

    # --- Méthode ---
    flow.append(PageBreak())
    flow.extend(_method_section(st))

    doc.build(flow)
    log.info("rapport écrit : %s (%.0f Ko)", path, path.stat().st_size / 1024)
    return path


def _signal_section(sig: dict, df, narrative: str, st: dict) -> list:
    """Une configuration : graphique, texte, décomposition du score."""
    out: list = []
    sens = {"long": "Achat", "short": "Vente"}[sig["direction"]]
    color = UP if sig["direction"] == "long" else DOWN
    out.append(Paragraph(
        f'{sig["label"]} ({sig["symbol"]}) — '
        f'<font color="#{color.hexval()[2:]}">{sens}</font> · '
        f'conviction {sig["score"]:.0f}/100', st["h2"]))

    if df is not None and len(df) > 30:
        out.append(_img(charts.price_chart(df, sig, title=False)))

    if sig["stop"] > 0:
        digits = 2 if sig["price"] >= 1000 else 4 if sig["price"] >= 1 else 8
        w = PAGE_W - 2 * MARGIN
        risk = abs(sig["entry"] - sig["stop"]) / sig["entry"] * 100
        out.append(Spacer(1, 4))
        out.append(_table(
            ["Entrée", "Invalidation", "Objectif", "R/R", "Risque", "Volatilité", "Régime"],
            [[f"{sig['entry']:,.{digits}f}", f"{sig['stop']:,.{digits}f}",
              f"{sig['target']:,.{digits}f}", f"{sig['rr']:.2f}", f"{risk:.2f} %",
              f"{sig['atr_pct']:.2f} %", sig["regime"]["name"].replace("_", " ")]],
            [w * .14, w * .14, w * .14, w * .08, w * .11, w * .12, w * .17],
            aligns={0: "RIGHT", 1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))

    # Les deux graphiques et le tableau restent groupés ; le texte s'écoule
    # ensuite librement. Garder le texte solidaire du graphique de facteurs
    # rejetait l'ensemble à la page suivante et laissait une page à moitié vide.
    out.append(Spacer(1, 2))
    out.append(_img(charts.factor_chart(sig)))
    out.append(Spacer(1, 4))
    out.extend(_para(narrative, st["body"]))

    if sig.get("warnings"):
        out.append(Paragraph("Réserves : " + " ; ".join(_escape(x) for x in sig["warnings"]),
                             st["small"]))
    out.append(Spacer(1, 12))
    return out


def _performance_section(perf: dict, portfolio: dict, trades: list[dict],
                         st: dict, w: float) -> list:
    out: list = []
    if perf.get("trades", 0) == 0:
        out.append(Paragraph(
            "Aucun trade n'a encore été fermé. Les statistiques de performance "
            "apparaîtront dès que le portefeuille aura un historique — et non "
            "avant : publier un taux de réussite sur trois trades n'aurait "
            "aucune valeur statistique.", st["body"]))
    else:
        pf = perf.get("profit_factor")
        out.append(_kpi_row([
            ("Trades", str(perf["trades"]), INK),
            ("Réussite", f"{perf['win_rate']:.0f} %", INK),
            ("Facteur profit", f"{pf:.2f}" if pf else "—",
             _color_for((pf or 1) - 1)),
            ("Espérance", f"{perf['expectancy_r']:+.2f} R", _color_for(perf["expectancy_r"])),
            ("Drawdown", f"-{perf['max_drawdown_pct']:.1f} %", DOWN),
            ("Sharpe", f"{perf['sharpe']:.2f}", _color_for(perf["sharpe"])),
        ], st))
        out.append(Spacer(1, 8))
        out.append(Paragraph(
            "Le facteur de profit (gains bruts ÷ pertes brutes) doit dépasser "
            "1.0 pour que la stratégie soit rentable après coûts ; l'espérance "
            "en R indique le gain moyen par unité de risque engagée. Ces deux "
            "chiffres priment sur le taux de réussite, qui peut être élevé tout "
            "en masquant une stratégie perdante.", st["small"]))
        out.append(Spacer(1, 6))
        out.append(_img(charts.r_distribution(trades)))

    positions = portfolio.get("positions", [])
    if positions:
        out.append(Paragraph("Positions ouvertes", st["h2"]))
        rows = [[p["symbol"], p["direction"], f"{p['entry']:,.4f}", f"{p['stop']:,.4f}",
                 f"{p['target']:,.4f}", f"{p['risk_amount']:,.2f}", str(p["bars_held"]),
                 p.get("stop_note", "—")[:22]] for p in positions]
        out.append(_table(["Symbole", "Sens", "Entrée", "Stop", "Objectif", "Risque",
                           "Bougies", "Stop"], rows,
                          [w * .13, w * .09, w * .13, w * .13, w * .13, w * .11, w * .09, w * .19],
                          aligns={2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"}))

    if trades:
        out.append(Paragraph("Derniers trades fermés", st["h2"]))
        rows = [[t["symbol"], t["direction"], f"{t['entry']:,.4f}", f"{t['exit']:,.4f}",
                 f"{t['pnl']:+,.2f}", f"{t['r_multiple']:+.2f}", t["reason"],
                 t["closed_at"][:10]] for t in trades[:18]]
        out.append(_table(["Symbole", "Sens", "Entrée", "Sortie", "PnL", "R", "Motif", "Date"],
                          rows, [w * .13, w * .09, w * .13, w * .13, w * .12, w * .09,
                                 w * .12, w * .13],
                          aligns={2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))
    return out


def _method_section(st: dict) -> list:
    return [
        Paragraph("Méthode", st["h1"]),
        Paragraph(
            "Le moteur détermine d'abord le <b>régime de marché</b> à partir de "
            "l'ADX, de la qualité de l'ajustement linéaire du log-prix (R²) et "
            "de l'exposant de Hurst estimé par analyse R/S. Cette étape "
            "conditionne tout le reste : un croisement de moyennes mobiles est "
            "pertinent en tendance et trompeur en range, un RSI en survente "
            "signale un achat en range et une continuation baissière en tendance.",
            st["body"]),
        Paragraph(
            "Six facteurs sont ensuite notés indépendamment dans l'intervalle "
            "[-1, +1] — tendance, momentum, retour à la moyenne, volume, "
            "cassure, sentiment de presse — puis combinés selon un jeu de "
            "pondérations propre au régime détecté. Le résultat est modulé par "
            "la qualité du régime et par l'accord avec l'unité de temps "
            "supérieure. Chaque point de score est traçable jusqu'à la formule "
            "qui l'a produit ; les graphiques de décomposition présentés plus "
            "haut sont la sortie directe de ce calcul.", st["body"]),
        Paragraph(
            "Les niveaux d'invalidation sont calés sur l'ATR et non sur un "
            "pourcentage fixe, car un stop à 2 % n'a pas le même sens sur une "
            "paire forex qui varie de 0.4 % par jour et sur un memecoin qui "
            "varie de 30 %. Le dimensionnement part du risque accepté, pas du "
            "montant investi : la taille découle de la distance au stop, et "
            "plusieurs plafonds — risque total du portefeuille, exposition "
            "unitaire, risque cumulé entre actifs corrélés — priment sur le "
            "score.", st["body"]),
        Paragraph(
            "Le portefeuille papier applique frais et glissement à l'entrée "
            "comme à la sortie, et détecte les sorties sur les extrêmes des "
            "bougies écoulées plutôt que sur le dernier cours. Lorsqu'une même "
            "bougie touche le stop et l'objectif, le stop est retenu : sans "
            "données infra-bougie, l'hypothèse favorable relèverait de "
            "l'auto-illusion.", st["body"]),
        Paragraph(
            "Limites connues : les données de volume sont indisponibles sur le "
            "forex via la source utilisée, ce qui neutralise ce facteur ; "
            "l'estimateur de Hurst surestime structurellement sa valeur sur les "
            "historiques courts et ne doit être lu qu'en comparaison ; le "
            "sentiment de presse repose sur un lexique anglophone et ne couvre "
            "pas les sources non listées. Aucun résultat passé, réel ou simulé, "
            "ne préjuge des performances futures.", st["body"]),
    ]
