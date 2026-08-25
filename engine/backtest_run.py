#!/usr/bin/env python3
"""Validation walk-forward du moteur, sur historique réel.

Répond à la seule question qui compte : le score a-t-il un pouvoir prédictif ?

Usage :
    python engine/backtest_run.py                 # univers complet
    python engine/backtest_run.py --bars 8000     # historique plus profond
    python engine/backtest_run.py --step 8        # plus rapide, moins de trades
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import backtest  # noqa: E402
from jimbot.config import DATA_DIR, UNIVERSE, Asset  # noqa: E402
from jimbot.datasources import crypto, yahoo  # noqa: E402
from jimbot.datasources.base import DataError  # noqa: E402
from jimbot.store import now_iso, write  # noqa: E402

log = logging.getLogger("jimbot.backtest")


def load(asset: Asset, bars: int):
    """Charge l'historique le plus profond disponible pour cet actif."""
    try:
        if asset.source == "binance":
            return crypto.klines_history(asset.ref, "1h", bars)
        return yahoo.chart(asset.ref, "1h", bars)
    except DataError as e:
        log.warning("%s ignoré : %s", asset.symbol, e)
        return None


def _worker(args):
    """Exécuté dans un processus séparé : l'analyse est gourmande en CPU."""
    asset, records, step = args
    import pandas as pd
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df.pop("t"), utc=True)
    return asset.symbol, backtest.run_asset(asset, df, step=step)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest walk-forward Jimbot")
    parser.add_argument("--bars", type=int, default=5000, help="profondeur d'historique")
    parser.add_argument("--step", type=int, default=backtest.STEP,
                        help="pas d'avancement, en bougies")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--symbols", type=str, default="",
                        help="liste de symboles séparés par des virgules")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    univers = UNIVERSE
    if args.symbols:
        voulus = {s.strip().upper() for s in args.symbols.split(",")}
        univers = [a for a in UNIVERSE if a.symbol.upper() in voulus]

    log.info("--- chargement de l'historique (%d bougies max) ---", args.bars)
    charges = []
    for asset in univers:
        df = load(asset, args.bars)
        if df is None or len(df) < backtest.WINDOW + 60:
            continue
        log.info("%-9s %5d bougies  %s → %s", asset.symbol, len(df),
                 df.index[0].date(), df.index[-1].date())
        # Les DataFrame ne se transmettent pas efficacement entre processus :
        # on passe des enregistrements simples. Le nom de l'index varie selon
        # la source (« open_time » chez Binance, absent chez Yahoo) : on le
        # fixe explicitement, sinon le renommage échoue silencieusement côté
        # source crypto et tous ces actifs sont perdus.
        plat = df.copy()
        plat.index.name = "t"
        records = plat.reset_index().to_dict("list")
        charges.append((asset, records, args.step))

    if not charges:
        log.error("aucun historique exploitable")
        return 1

    log.info("--- simulation walk-forward (%d actifs, pas de %d) ---",
             len(charges), args.step)
    tous: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, c): c[0].symbol for c in charges}
        for fut in as_completed(futures):
            try:
                _, trades = fut.result()
                tous.extend(trades)
            except Exception as e:  # noqa: BLE001
                log.error("%s : échec (%s)", futures[fut], e)

    log.info("--- %d trade(s) simulé(s) ---", len(tous))
    if not tous:
        log.error("aucun trade : le seuil est peut-être trop élevé pour cet historique")
        return 1

    calib = backtest.calibration(tous)
    distance = backtest.edge_by_distance(tous)
    structure = backtest.structure_effect(tous)

    rapport = {
        "generated_at": now_iso(),
        "parametres": {"bars": args.bars, "step": args.step,
                       "window": backtest.WINDOW, "actifs": len(charges)},
        "calibration": calib,
        "avantage_par_distance": distance,
        "effet_de_la_structure": structure,
        "limites": [
            "Le sentiment de presse est neutralisé : le flux d'actualité "
            "historique n'est pas reconstituable. Seule la partie technique "
            "est mesurée.",
            "Sans données infra-bougie, une bougie touchant stop et objectif "
            "est comptée comme un stop.",
            "Les trades se chevauchent : l'espérance est correcte, mais le "
            "drawdown en R suppose une exposition unitaire constante.",
        ],
        # Un échantillon de trades suffit à refaire les calculs ; en garder
        # plusieurs milliers alourdirait le dépôt à chaque exécution.
        "trades": tous[:500],
    }
    write("backtest", rapport)

    print()
    print("=" * 74)
    print(f"{'CALIBRATION':^74}")
    print("=" * 74)
    print(f"  trades simulés          {calib['trades']}")
    print(f"  taux de réussite        {calib['win_rate_global']} %  "
          f"(modèle : {calib['prob_predite_moyenne']} %)")
    print(f"  espérance réalisée      {calib['esperance_realisee']:+.3f} R  "
          f"(modèle : {calib['esperance_predite']:+.3f} R)")
    print(f"  facteur de profit       {calib.get('facteur_de_profit')}")
    if "ic95" in calib:
        print(f"  intervalle 95 %         [{calib['ic95'][0]:+.3f} ; {calib['ic95'][1]:+.3f}] R")
        print(f"  significativité         {calib['verdict']}")
    print(f"  drawdown max            {calib.get('drawdown_max_R')} R")
    if "correlation_score_esperance" in calib:
        print(f"  corrélation score/gain  {calib['correlation_score_esperance']:+.3f}")
    print()
    print(f"  {'tranche':<10} {'n':>5} {'réussite':>10} {'prédit':>8} "
          f"{'E réalisée':>11} {'E prédite':>10}")
    for t in calib.get("par_tranche_de_score", []):
        print(f"  {t['tranche']:<10} {t['trades']:>5} {t['win_rate']:>9.1f}% "
              f"{t['prob_predite']:>7.1f}% {t['esperance_realisee']:>+11.3f} "
              f"{t['esperance_predite']:>+10.3f}")
    print()
    if distance:
        print(f"  {'distance obj.':<14} {'n':>5} {'réussite':>10} {'prédit':>8} "
              f"{'biais':>8} {'E réelle':>10}")
        for d in distance:
            print(f"  {d['distance_atr']:<14} {d['trades']:>5} {d['win_rate']:>9.1f}% "
                  f"{d['prob_predite']:>7.1f}% {d['biais']:>+7.1f}pt "
                  f"{d['esperance_realisee']:>+10.3f}")
    print()
    if structure and "note" not in structure:
        print(f"  {'stop adossé à':<26} {'n':>5} {'réussite':>10} {'seuil':>8} "
              f"{'écart':>8} {'E réelle':>10}")
        for cle, v in structure.items():
            print(f"  {cle.replace('_', ' '):<26} {v['trades']:>5} "
                  f"{v['win_rate']:>9.1f}% {v['seuil_rentabilite']:>7.1f}% "
                  f"{v['ecart_au_seuil']:>+7.1f}pt {v['esperance']:>+10.3f}")
    print("=" * 74)
    log.info("rapport écrit dans %s", DATA_DIR / "backtest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
