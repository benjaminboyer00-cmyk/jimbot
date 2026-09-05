#!/usr/bin/env python3
"""Le scalping court a-t-il un avantage qui survit aux frais ?

Le moteur travaille en horaire. La question posée ici est différente : sur les
actifs les plus volatils, à un horizon de quelques dizaines de minutes, reste-t-il
quelque chose une fois le péage payé ?

Elle se décompose en deux, et il faut les deux :

1. **Y a-t-il de l'information ?** C'est le coefficient d'information, que
   `probe` mesure déjà.
2. **Vaut-elle plus que le péage ?** C'est `probe.edge_net`. Un IC honorable sur
   un rendement dont l'écart-type vaut trois points de base ne paiera jamais
   vingt points de base de frais. À horizon court, c'est cette question qui
   tranche, et elle est indépendante de la qualité du modèle.

Usage :
    python engine/scalp_run.py                  # crypto, 5 min
    python engine/scalp_run.py --interval 15m
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import probe  # noqa: E402
from jimbot.config import UNIVERSE, Asset  # noqa: E402
from jimbot.datasources import crypto  # noqa: E402
from jimbot.datasources.base import DataError  # noqa: E402
from jimbot.store import now_iso, write  # noqa: E402
from jimbot.strategy import MEASURED_WEIGHTS  # noqa: E402

log = logging.getLogger("jimbot.scalp")

# Le sentiment n'est pas reconstituable sur l'historique : il est exclu de la
# combinaison mesurée, comme il l'est déjà de la sonde.
POIDS = {k: v for k, v in MEASURED_WEIGHTS.items() if k != "sentiment"}


def actifs_volatils() -> list[Asset]:
    """Les actifs à forte volatilité, seuls candidats crédibles au scalping.

    Ce sont aussi les seuls dont on peut obtenir un historique cinq minutes
    profond et gratuit : Yahoo plafonne les granularités fines à soixante jours
    et ne les sert pas de façon fiable sur les indices.
    """
    return [a for a in UNIVERSE if a.source == "binance"]


def main() -> int:
    p = argparse.ArgumentParser(description="Avantage net du scalping court")
    p.add_argument("--interval", default="5m", choices=sorted(probe.HORIZONS_PAR_INTERVALLE))
    p.add_argument("--bars", type=int, default=12000)
    p.add_argument("--step", type=int, default=4)
    p.add_argument("--out", default="scalp")
    p.add_argument("--cache", default="",
                   help="fichier JSONL d'observations : relu s'il existe, "
                        "écrit sinon. La sonde coûte 25 min, l'analyse 3 s.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    horizons = probe.HORIZONS_PAR_INTERVALLE[args.interval]
    minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}[args.interval]

    lignes: list[dict] = []
    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        with cache.open(encoding="utf-8") as fh:
            lignes = [json.loads(l) for l in fh if l.strip()]
        log.info("%d observations relues depuis %s", len(lignes), cache)

    for a in ([] if lignes else actifs_volatils()):
        try:
            df = crypto.klines_history(a.ref, args.interval, args.bars)
        except DataError as e:
            log.warning("%s ignoré : %s", a.symbol, e)
            continue
        if len(df) < probe.WINDOW + max(horizons) + 10:
            log.warning("%s : historique trop court (%d)", a.symbol, len(df))
            continue
        obs = probe.probe_asset(a, df, step=args.step, horizons=horizons)
        lignes.extend(obs)
        log.info("%-10s %5d bougies -> %4d observations", a.symbol, len(df), len(obs))

    if len(lignes) < 500:
        log.error("échantillon insuffisant (%d)", len(lignes))
        return 1

    if cache and not cache.exists():
        with cache.open("w", encoding="utf-8") as fh:
            for r in lignes:
                fh.write(json.dumps(r) + "\n")
        log.info("observations mises en cache dans %s", cache)

    probe.score_combine(lignes, POIDS)
    ic = probe.information_coefficients(lignes, horizons)
    net = probe.edge_net(lignes, "score", horizons)

    resultat = {
        "generated_at": now_iso(),
        "intervalle": args.interval,
        "minutes_par_bougie": minutes,
        "actifs": sorted({r["symbol"] for r in lignes}),
        "observations": len(lignes),
        "poids": POIDS,
        "ic": ic,
        "avantage_net": net,
    }
    write(args.out, resultat)

    # --- Restitution lisible ---------------------------------------------
    print()
    print(f"  {len(lignes)} observations, {len(resultat['actifs'])} actifs, "
          f"bougies de {minutes} min")
    print()
    print("  Pouvoir prédictif des facteurs (coefficient d'information)")
    print(f"    {'facteur':16} {'IC max':>8} {'horizon':>8} {'significatif':>13}")
    for nom, v in ic.get("par_facteur", {}).items():
        h = v["meilleur_horizon"].removeprefix("h")
        print(f"    {nom:16} {v['ic_max']:+8.4f} {h + ' bougies':>8} "
              f"{'oui' if v['significatif'] else 'non':>13}")

    print()
    print("  Avantage du quintile, en points de base par aller-retour")
    print(f"    {'horizon':>10} {'paris':>7} {'moyenne':>9} {'mediane':>9} "
          f"{'elaguee':>9} {'gagnants':>9} {'pire':>7}")
    for h, e in net.get("par_horizon", {}).items():
        mn = int(h.removeprefix('h')) * minutes
        print(f"    {str(mn) + ' min':>10} {e['n_paris']:7d} {e['brut_pb']:+9.2f} "
              f"{e['mediane_pb']:+9.2f} {e['moyenne_elaguee_pb']:+9.2f} "
              f"{e['part_gagnante_pct']:8.1f}% {e['pire_pb']:+7.0f}")

    print()
    print("  La moyenne est ce qu'on encaisse ; la médiane est ce qu'on ressent.")
    print("  Un écart entre les deux signale un gain régulier annulé par ses queues.")

    plafond = net.get("cout_maximal_supportable_pb")
    if plafond is not None:
        print()
        print(f"  Coût maximal supportable : {plafond:+.2f} pb par aller-retour")
        print(f"  Tarifs réels             : CFD serré 10 pb, Binance taker 20 pb")
        rentable = any(c["rentable"] for e in net["par_horizon"].values()
                       for c in e["net_pb"].values())
        print(f"  Verdict                  : "
              f"{'un avantage net subsiste' if rentable else 'aucun avantage net'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
