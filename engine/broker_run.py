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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import broker  # noqa: E402
from jimbot.config import UNIVERSE  # noqa: E402
from jimbot.store import read  # noqa: E402

log = logging.getLogger("jimbot.broker")


def charger_env(chemin: Path) -> int:
    """Charge un fichier .env dans l'environnement du processus.

    Existe pour que le jeton n'ait jamais à être tapé dans un terminal : un
    `export METAAPI_TOKEN=...` reste dans l'historique du shell, lisible par
    tout ce qui tourne sous ce compte, et remonte à la moindre capture d'écran
    d'un terminal. Le fichier, lui, est ignoré par git et refusé par le crochet
    de pre-commit.

    Les variables déjà présentes dans l'environnement gagnent : en intégration
    continue, ce sont les secrets du dépôt qui font foi, et aucun fichier local
    ne doit pouvoir les remplacer.
    """
    if not chemin.exists():
        return 0
    n = 0
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle, valeur = cle.strip(), valeur.strip().strip('"').strip("'")
        if valeur and not os.environ.get(cle):
            os.environ[cle] = valeur
            n += 1
    return n


def check() -> int:
    try:
        api = broker._client()
        c = api.compte()
    except broker.BrokerError as e:
        print(f"\n  ÉCHEC : {e}\n")
        if os.environ.get("JIMBOT_BROKER_TYPE", "capital").lower() == "capital":
            _diagnostic_capital()
        return 1

    # L'environnement se lit d'abord, et en toutes lettres : c'est la seule
    # chose qui distingue un essai d'une perte réelle, et un montage se
    # configure une fois pour tourner pendant des mois.
    base = getattr(api, "base", "")
    demo = c.est_demo
    print()
    if demo:
        print("  ┌────────────────────────────────────────────────┐")
        print("  │  DÉMONSTRATION — aucun argent réel n'est engagé │")
        print("  └────────────────────────────────────────────────┘")
    else:
        print("  ┌────────────────────────────────────────────────┐")
        print("  │  COMPTE RÉEL — de l'argent réel est en jeu      │")
        print("  └────────────────────────────────────────────────┘")
    print(f"  Adresse    {base}")
    print(f"  Courtier   {c.courtier}")
    print(f"  Serveur    {c.serveur}")
    print(f"  Compte     {c.login}")

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


def _diagnostic_capital() -> None:
    """Isole la cause d'un refus de session en comparant les deux environnements.

    La clé est la même pour la démonstration et le réel : si l'un accepte et
    l'autre refuse, le problème est l'environnement ; si les deux refusent, ce
    sont les identifiants. Seules des sessions sont ouvertes — aucune lecture de
    compte, aucun ordre.
    """
    from jimbot.broker_capital import diagnostiquer

    cle = os.environ.get("CAPITAL_API_KEY", "")
    ident = os.environ.get("CAPITAL_IDENTIFIER", "")
    mdp = os.environ.get("CAPITAL_PASSWORD", "")
    print("  Diagnostic — ouverture d'une session sur les deux environnements")
    print(f"    clé          {'renseignée (' + str(len(cle)) + ' caractères)' if cle else 'ABSENTE'}")
    print(f"    identifiant  {ident or 'ABSENT'}")
    print(f"    mot de passe {'renseigné (' + str(len(mdp)) + ' caractères)' if mdp else 'ABSENT'}")
    print()

    res = diagnostiquer(cle, ident, mdp)
    for nom, r in res.items():
        etat = "ACCEPTÉE" if r["ok"] else "refusée"
        print(f"    {nom:14} {etat}")
        if not r["ok"]:
            print(f"                   {r['detail'][:160]}")

    demo_ok = res["démonstration"]["ok"]
    reel_ok = res["réel"]["ok"]
    print()
    if demo_ok:
        print("  La démonstration accepte vos identifiants : relancez --check.")
    elif reel_ok:
        print("  Vos identifiants sont bons, mais seul l'environnement RÉEL les")
        print("  accepte. Capital.com n'ouvre l'accès démo qu'une fois un compte")
        print("  de démonstration créé dans l'application : basculez sur « Démo »")
        print("  dans la plateforme, puis réessayez.")
    else:
        print("  Les deux refusent : ce sont les identifiants.")
        print("    - CAPITAL_IDENTIFIER doit être l'e-mail du compte.")
        print("    - CAPITAL_PASSWORD est le mot de passe défini À LA CRÉATION")
        print("      DE LA CLÉ, jamais celui du compte. C'est la cause la plus")
        print("      fréquente d'un 401.")
        print("    - Une clé fraîchement créée peut mettre quelques minutes.")


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

    racine = Path(__file__).resolve().parents[1]
    n = charger_env(racine / ".env")
    if n:
        log.info("%d variable(s) lue(s) depuis .env", n)
    if args.check:
        return check()
    return sync(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
