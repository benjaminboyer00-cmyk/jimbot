#!/usr/bin/env python3
"""Rapport quotidien — exécuté une fois par jour par GitHub Actions.

Reprend le dernier instantané, recalcule les performances, rédige le
briefing et produit le PDF, puis le publie sur Discord.

Usage :
    python engine/daily_report.py
    python engine/daily_report.py --no-send   # génère le PDF sans publier
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import discord, narrator, pipeline, report  # noqa: E402
from jimbot.datasources import news as news_src  # noqa: E402
from jimbot.paper import performance  # noqa: E402
from jimbot.store import read, write, now_iso  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport quotidien Jimbot")
    parser.add_argument("--no-send", action="store_true", help="ne publie pas sur Discord")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("jimbot.report")

    # On recollecte : le rapport a besoin des bougies pour tracer les graphiques,
    # et l'instantané JSON ne les contient pas (trop volumineux pour git).
    log.info("--- collecte ---")
    data = pipeline.collect()
    if not data["candles"]:
        log.error("aucune donnée de marché, rapport annulé")
        return 1

    signals = pipeline.analyze_all(data)
    corr = pipeline.correlations(data)

    portfolio = read("portfolio", {}) or {}
    trades = read("trades", []) or []
    perf = performance(trades, portfolio.get("equity_curve", []),
                       portfolio.get("initial", 10000.0))

    regimes: dict[str, int] = {}
    for s in signals:
        regimes[s["regime"]["name"]] = regimes.get(s["regime"]["name"], 0) + 1

    log.info("--- rédaction ---")
    briefing, engine = narrator.narrate_briefing(
        perf, portfolio, [s for s in signals if s["direction"] != "neutre"],
        regimes, [a.to_dict() for a in data["articles"][:10]],
        risk_off=data.get("risk_off", {}))
    log.info("briefing rédigé via %s (%d caractères)", engine, len(briefing))

    narratives = {}
    for sig in [s for s in signals if s["direction"] != "neutre"][:6]:
        text, _ = narrator.narrate_signal(sig)
        narratives[sig["symbol"]] = text

    log.info("--- génération du PDF ---")
    path = report.build(
        signals=signals, candles=data["candles"], briefing=briefing,
        narratives=narratives, portfolio=portfolio, performance=perf,
        trades=trades, news=[a.to_dict() for a in data["articles"]],
        memecoins=[m.to_dict() | {"health_score": m.health_score}
                   for m in data["memecoins"]],
        meme_report=data.get("meme_report", {}),
        risk_off=data.get("risk_off", {}),
        speeches=news_src.major_speeches(data["articles"]),
        corr=corr, engine=engine,
    )
    log.info("PDF : %s (%.0f Ko)", path, path.stat().st_size / 1024)

    write("last_report", {"path": str(path.relative_to(path.parents[1])),
                          "generated_at": now_iso(), "engine": engine,
                          "briefing": briefing})

    if args.no_send:
        log.info("publication désactivée (--no-send)")
        return 0

    log.info("--- publication Discord ---")
    discord.send_briefing(briefing, title="Briefing de marché quotidien")
    stats = {"Capital": f"{portfolio.get('equity', 0):,.2f}",
             "Trades": perf.get("trades", 0),
             "Réussite": f"{perf.get('win_rate', 0):.0f} %",
             "Espérance": f"{perf.get('expectancy_r', 0):+.2f} R",
             "Signaux": sum(1 for s in signals if s["direction"] != "neutre")}
    discord.send_report(path, summary="Analyse complète en pièce jointe.", stats=stats)
    log.info("rapport publié")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
