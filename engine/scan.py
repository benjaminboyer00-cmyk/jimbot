#!/usr/bin/env python3
"""Scan de marché — exécuté toutes les 15 minutes par GitHub Actions.

Analyse l'univers, fait vivre le portefeuille papier, publie les alertes
Discord au-dessus du seuil, et écrit les fichiers JSON du dashboard.

Usage :
    python engine/scan.py            # scan complet
    python engine/scan.py --no-alert # analyse et écriture, sans Discord
    python engine/scan.py --dry-run  # simule aussi les envois
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import discord, narrator, pipeline  # noqa: E402
from jimbot.config import SETTINGS  # noqa: E402
from jimbot.paper import performance  # noqa: E402
from jimbot.store import read  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan de marché Jimbot")
    parser.add_argument("--no-alert", action="store_true",
                        help="n'envoie aucune alerte Discord")
    parser.add_argument("--dry-run", action="store_true",
                        help="simule les envois Discord dans les journaux")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("jimbot.scan")
    if args.dry_run:
        import os
        os.environ["JIMBOT_DRY_RUN"] = "1"

    log.info("--- collecte ---")
    data = pipeline.collect()
    if not data["candles"]:
        log.error("aucune donnée de marché récupérée, scan interrompu")
        return 1

    log.info("--- analyse ---")
    signals = pipeline.analyze_all(data)
    corr = pipeline.correlations(data)

    log.info("--- portefeuille papier ---")
    portfolio, closed = pipeline.run_paper(signals, data, corr)

    snapshot = pipeline.persist_scan(signals, data, portfolio, closed)
    counts = snapshot["counts"]
    log.info("%d actifs analysés · %d signaux (%d achat / %d vente)",
             counts["analysed"], counts["actionable"], counts["long"], counts["short"])

    # --- Alertes ---
    if args.no_alert:
        log.info("alertes désactivées (--no-alert)")
    else:
        # Le contexte passe avant les signaux : un discours de banque centrale
        # ou une escalade géopolitique explique souvent les signaux qui suivent.
        from jimbot.datasources import news as news_src
        for speech in news_src.major_speeches(data["articles"]):
            if discord.send_speech_alert(speech):
                log.info("alerte discours publiée : %s (ton %+.1f, importance %.2f)",
                         speech["speaker"], speech["tone"], speech["importance"])
        if discord.send_geopolitical_alert(data.get("risk_off", {})):
            log.info("alerte géopolitique publiée : tension %+.2f",
                     data["risk_off"]["level"])

        to_alert = [s for s in signals
                    if s["direction"] != "neutre" and s["score"] >= SETTINGS.alert_threshold]
        log.info("--- %d alerte(s) au-dessus du seuil de %.0f ---",
                 len(to_alert), SETTINGS.alert_threshold)
        for sig in to_alert:
            narrative, engine = narrator.narrate_signal(sig)
            if discord.send_signal(sig, narrative):
                log.info("alerte publiée : %s %s (%.0f) via %s",
                         sig["direction"], sig["symbol"], sig["score"], engine)

        # Résumé des sorties : sans cela, le salon ne voit que les entrées.
        if closed:
            lines = [f"• {t['label']} {t['direction']} — {t['reason']}, "
                     f"{t['pnl']:+.2f} ({t['r_multiple']:+.2f} R)" for t in closed]
            perf = performance(read("trades", []) or [],
                               portfolio.get("equity_curve", []), portfolio["initial"])
            discord.send_briefing(
                "\n\n".join(lines), title=f"{len(closed)} position(s) clôturée(s)",
                stats={"Capital": f"{portfolio['equity']:,.2f}",
                       "Trades": perf.get("trades", 0),
                       "Espérance": f"{perf.get('expectancy_r', 0):+.2f} R"})

    log.info("scan terminé")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
