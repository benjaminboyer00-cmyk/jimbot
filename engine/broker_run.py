#!/usr/bin/env python3
"""Raccordement du moteur à un compte MetaTrader, via MetaApi.

Trois usages, du plus inoffensif au plus engageant :

    python engine/broker_run.py --check      # lit le compte, ne touche à rien
    python engine/broker_run.py --dry-run    # calcule les ordres, n'en passe aucun
    python engine/broker_run.py --sync       # passe réellement les ordres

`--check` est le premier à lancer : il dit si le jeton fonctionne, si le compte
est bien un compte de démonstration, quel courtier le tient, et lesquels de vos
instruments il connaît. Aucun ordre ne part.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import broker  # noqa: E402
from jimbot.config import UNIVERSE  # noqa: E402
from jimbot.store import read  # noqa: E402

log = logging.getLogger("jimbot.broker")


def check() -> int:
    try:
        api = broker._client()
        c = api.compte()
    except broker.BrokerError as e:
        print(f"\n  ÉCHEC : {e}\n")
        print("  Vérifiez METAAPI_TOKEN, METAAPI_ACCOUNT_ID et METAAPI_REGION.")
        return 1

    print()
    print(f"  Courtier   {c.courtier}")
    print(f"  Serveur    {c.serveur}")
    print(f"  Compte     {c.login}")
    print(f"  Type       {c.type_compte}"
          f"{'   <- compte de démonstration' if c.est_demo else '   <- COMPTE RÉEL'}")
    print(f"  Solde      {c.solde:,.2f} {c.devise}   (équité {c.equite:,.2f})")
    print(f"  Trading    {'autorisé' if c.trading_autorise else 'REFUSÉ par le courtier'}")

    if not c.est_demo:
        print()
        print("  Ce compte n'est pas un compte de démonstration. Aucun ordre ne sera")
        print("  transmis tant que JIMBOT_BROKER_ALLOW_LIVE n'est pas explicitement posé.")

    print()
    print("  Instruments reconnus chez ce courtier")
    connus = manquants = 0
    for a in UNIVERSE:
        nom = api.resoudre(a.symbol)
        if nom:
            connus += 1
            print(f"    {a.symbol:10} -> {nom}")
        else:
            manquants += 1
            print(f"    {a.symbol:10} -> absent")
    print(f"\n  {connus} reconnu(s), {manquants} absent(s).")

    positions = api.positions()
    print(f"  {len(positions)} position(s) ouverte(s) sur le compte.")
    for p in positions:
        print(f"    {p.get('symbol')} {p.get('type')} {p.get('volume')} "
              f"profit {p.get('profit')}")
    print()
    return 0


def sync(dry_run: bool) -> int:
    snap = read("latest", None)
    if not snap:
        print("  Aucun scan disponible : lancez d'abord engine/scan.py.")
        return 1

    signaux = [s for s in snap.get("signals", []) if s.get("actionable")]
    print(f"\n  {len(signaux)} configuration(s) retenue(s) dans le scan du "
          f"{snap.get('generated_at', '?')[:16]}")
    if not signaux:
        print("  Rien à transmettre. Le moteur reste à l'écart — c'est le cas le")
        print("  plus fréquent : 5,6 % des relevés franchissent le seuil.\n")
        return 0

    rapport = broker.synchroniser(signaux, dry_run=dry_run)
    if rapport.get("erreur"):
        print(f"\n  ARRÊT : {rapport['erreur']}\n")
        return 1

    for o in rapport["ordres"]:
        marque = "SIMULÉ " if o.get("simule") else "TRANSMIS"
        print(f"  {marque} {o['actionType']:16} {o['symbol']:10} {o['volume']:>8} lot(s)"
              f"  SL {o['stopLoss']}  TP {o['takeProfit']}")
    for i in rapport["ignores"]:
        print(f"  ignoré   {i['symbol']:10} — {i['raison']}")
    print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Raccordement MetaTrader")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="lire le compte, ne rien faire")
    g.add_argument("--dry-run", action="store_true", help="calculer les ordres sans les passer")
    g.add_argument("--sync", action="store_true", help="passer réellement les ordres")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    if args.check:
        return check()
    return sync(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
