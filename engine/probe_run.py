#!/usr/bin/env python3
"""Mesure du pouvoir prédictif de chaque facteur.

Répond à la question qui précède toutes les autres : qu'est-ce qui, dans ce
moteur, porte réellement de l'information ?

Usage :
    python engine/probe_run.py                  # univers complet
    python engine/probe_run.py --step 2         # échantillon plus dense
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import probe  # noqa: E402
from jimbot.config import DATA_DIR, UNIVERSE, Asset  # noqa: E402
from jimbot.datasources import crypto, yahoo  # noqa: E402
from jimbot.datasources.base import DataError  # noqa: E402
from jimbot.store import now_iso, write  # noqa: E402

log = logging.getLogger("jimbot.probe")


def load(asset: Asset, bars: int, interval: str = "1h"):
    try:
        if asset.source == "binance":
            return crypto.klines_history(asset.ref, interval, bars)
        if interval == "4h":
            # Yahoo ne propose pas cette granularité : on agrège l'horaire.
            return yahoo.resample_4h(yahoo.chart(asset.ref, "1h", bars * 4))
        return yahoo.chart(asset.ref, interval, bars)
    except DataError as e:
        log.warning("%s ignoré : %s", asset.symbol, e)
        return None


def _worker(args):
    asset, records, step, horizons = args
    import pandas as pd
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df.pop("t"), utc=True)
    return probe.probe_asset(asset, df, step=step, horizons=horizons)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sonde de pouvoir prédictif")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--interval", type=str, default="1h",
                        choices=sorted(probe.HORIZONS_PAR_INTERVALLE))
    parser.add_argument("--out", type=str, default="probe",
                        help="nom du fichier de sortie dans data/")
    args = parser.parse_args()

    horizons = probe.HORIZONS_PAR_INTERVALLE[args.interval]

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    log.info("--- chargement (%s, horizons %s) ---", args.interval, horizons)
    charges = []
    for asset in UNIVERSE:
        df = load(asset, args.bars, args.interval)
        if df is None or len(df) < probe.WINDOW + 80:
            continue
        plat = df.copy()
        plat.index.name = "t"
        charges.append((asset, plat.reset_index().to_dict("list"), args.step, horizons))
    if not charges:
        log.error("aucun historique")
        return 1

    log.info("--- sonde (%d actifs) ---", len(charges))
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, c): c[0].symbol for c in charges}
        for fut in as_completed(futures):
            try:
                rows.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                log.error("%s : %s", futures[fut], e)

    log.info("--- %d observation(s) ---", len(rows))
    if not rows:
        return 1

    # Les observations sont écrites avant l'analyse : une erreur dans le
    # calcul ne doit pas faire perdre plusieurs minutes de simulation.
    write(f"{args.out}_raw", {"generated_at": now_iso(),
                             "interval": args.interval,
                             "horizons": list(horizons),
                             "rows": rows[:20000]})

    ics = probe.information_coefficients(rows, horizons)
    par_regime = probe.ic_by_regime(rows, horizon=horizons[-2])
    poids = probe.derived_weights(par_regime)

    write(args.out, {
        "generated_at": now_iso(),
        "parametres": {"bars": args.bars, "step": args.step,
                       "interval": args.interval, "horizons": list(horizons),
                       "actifs": len(charges)},
        "coefficients": ics,
        "par_regime": par_regime,
        "poids_derives": poids,
        "note": ("Le coefficient d'information est la corrélation de rang entre "
                 "la valeur d'un facteur et le rendement futur normalisé par "
                 "l'ATR. Un IC de 0.02 à 0.05 est exploitable, au-delà de 0.10 "
                 "il est inhabituel, un IC nul signifie que le facteur est du "
                 "bruit. Le sentiment est absent : le flux de presse historique "
                 "n'est pas reconstituable."),
    })

    print()
    print("=" * 78)
    print(f"{'POUVOIR PRÉDICTIF PAR FACTEUR':^78}")
    print(f"{f'{len(rows)} observations':^78}")
    print("=" * 78)
    print(f"  {'facteur':<16} " + " ".join(f"{f'h{h}':>11}" for h in horizons))
    for nom, v in sorted(ics.get("par_facteur", {}).items(),
                         key=lambda kv: -abs(kv[1]["ic_max"])):
        cellules = []
        for h in horizons:
            e = v["horizons"].get(f"h{h}")
            if e is None:
                cellules.append(f"{'—':>11}")
            else:
                marque = "*" if e["significatif"] else " "
                cellules.append(f"{e['ic']:>+10.4f}{marque}")
        print(f"  {nom:<16} " + " ".join(cellules))
    print()
    print("  (* = statistiquement distinguable du bruit, |t| > 2)")
    print()
    print(f"{'  PAR RÉGIME (horizon 24 bougies)':<78}")
    for regime, mesures in par_regime.items():
        if "observations" not in mesures:
            continue
        print(f"\n  {regime}  ({mesures['observations']} observations)")
        for nom, e in sorted(((k, v) for k, v in mesures.items()
                              if isinstance(v, dict)),
                             key=lambda kv: -abs(kv[1]["ic"])):
            marque = "*" if abs(e["t"]) > 2 else " "
            print(f"    {nom:<16} IC {e['ic']:>+8.4f}{marque}  t={e['t']:>+6.2f}")
    print()
    print("  POIDS DÉDUITS DES MESURES")
    for regime, p in poids.items():
        retenus = {k: v for k, v in p.items() if not k.startswith("_") and v != 0}
        note = p.get("_note", "")
        print(f"    {regime:<22} {retenus if retenus else note}")
    print("=" * 78)
    log.info("rapport écrit dans %s", DATA_DIR / f"{args.out}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
