#!/usr/bin/env python3
"""Pousse le contenu de `.env` vers les secrets et variables GitHub Actions.

Le fichier `.env` ne doit jamais entrer dans le dépôt : il y serait lisible par
quiconque, pour toujours, et le retirer d'un commit ultérieur ne l'efface ni de
l'historique ni des clones déjà faits. Mais recopier sept valeurs à la main
dans une interface web est une invitation à la faute de frappe — sur un
identifiant de courtier, une faute de frappe se traduit par une panne qu'on met
une heure à comprendre.

Ce script fait le trajet correctement : il lit `.env` en local, chiffre chaque
secret avec la clé publique du dépôt (GitHub l'exige : le serveur ne voit
jamais la valeur en clair), et les dépose dans le magasin de secrets.

**Il n'affiche jamais une valeur**, ni dans sa sortie, ni dans une erreur. Un
script d'installation qui journalise le secret qu'il installe annule ce qu'il
sert à protéger.

La distinction entre secret et variable n'est pas cosmétique : une variable est
lisible dans les réglages du dépôt, un secret ne l'est plus une fois posé. Les
interrupteurs sont donc des variables — on doit pouvoir vérifier d'un coup
d'œil si l'exécution réelle est armée — et les identifiants des secrets.

Usage :
    export GITHUB_TOKEN=ghp_...        # portée « repo »
    python scripts/pousser_secrets.py --dry-run
    python scripts/pousser_secrets.py
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

# Ce qui est un secret, et ce qui n'en est pas.
SECRETS = {
    "CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD",
    "METAAPI_TOKEN", "METAAPI_ACCOUNT_ID",
    "DISCORD_WEBHOOK_URL", "DISCORD_ROLE_ID", "ANTHROPIC_API_KEY",
}
VARIABLES = {
    "JIMBOT_BROKER", "JIMBOT_BROKER_TYPE", "JIMBOT_BROKER_MAX_POSITIONS",
    "JIMBOT_BROKER_ALLOW_LIVE", "JIMBOT_RISK_MULT",
    "CAPITAL_DEMO", "METAAPI_REGION",
    "JIMBOT_SIGNAL_THRESHOLD", "JIMBOT_ALERT_THRESHOLD",
    "JIMBOT_PING_THRESHOLD", "JIMBOT_PAPER_CAPITAL",
}

# Réglages dont l'effet est d'engager de l'argent réel. Ils ne partent qu'avec
# un accord explicite : armer une exécution réelle par mégarde, en poussant un
# fichier de configuration, serait la pire façon de le faire.
ENGAGE_DE_L_ARGENT = {"JIMBOT_BROKER", "JIMBOT_BROKER_ALLOW_LIVE", "CAPITAL_DEMO"}


def lire_env(chemin: Path) -> dict[str, str]:
    if not chemin.exists():
        raise SystemExit(f"{chemin} introuvable.")
    valeurs = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        cle, val = cle.strip(), val.strip().strip('"').strip("'")
        if val:
            valeurs[cle] = val
    return valeurs


def depot() -> str:
    """Propriétaire/nom du dépôt, déduit de l'origine git."""
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=RACINE,
                         capture_output=True, text=True, check=True).stdout.strip()
    m = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?$", url)
    if not m:
        raise SystemExit(f"origine git non reconnue : {url}")
    return m.group(1)


def api(token: str, methode: str, chemin: str, corps: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{chemin}", method=methode,
        data=json.dumps(corps).encode() if corps is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            brut = r.read()
            return json.loads(brut) if brut else {}
    except urllib.error.HTTPError as e:
        # Le corps d'erreur de GitHub ne contient jamais la valeur envoyée,
        # seulement un motif de refus : il est sûr à afficher.
        raise SystemExit(f"GitHub {e.code} sur {methode} {chemin} : "
                         f"{e.read().decode()[:200]}")


def chiffrer(valeur: str, cle_publique_b64: str) -> str:
    """Chiffrement scellé, tel que GitHub l'impose pour les secrets."""
    from nacl import encoding, public
    cle = public.PublicKey(cle_publique_b64.encode(), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(cle).encrypt(valeur.encode())).decode()


def main() -> int:
    p = argparse.ArgumentParser(description="Pousse .env vers GitHub Actions")
    p.add_argument("--env", default=str(RACINE / ".env"))
    p.add_argument("--dry-run", action="store_true",
                   help="montre ce qui partirait, n'envoie rien")
    p.add_argument("--armer-le-reel", action="store_true",
                   help="autorise l'envoi des réglages qui engagent de l'argent réel")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not args.dry_run:
        raise SystemExit("GITHUB_TOKEN absent. Créez-en un sur "
                         "https://github.com/settings/tokens avec la portée « repo ».")

    valeurs = lire_env(Path(args.env))
    nom = depot()
    print(f"\n  Dépôt   {nom}")
    print(f"  Source  {args.env}\n")

    inconnus = sorted(set(valeurs) - SECRETS - VARIABLES)
    a_pousser = []
    for cle, val in valeurs.items():
        if cle in SECRETS:
            genre = "secret"
        elif cle in VARIABLES:
            genre = "variable"
        else:
            continue
        arme = cle in ENGAGE_DE_L_ARGENT
        if arme and not args.armer_le_reel:
            print(f"  RETENU    {genre:9} {cle:32} "
                  f"(engage de l'argent réel — voir --armer-le-reel)")
            continue
        a_pousser.append((genre, cle, val, arme))
        # La longueur suffit à repérer une valeur tronquée sans la révéler.
        print(f"  à pousser {genre:9} {cle:32} ({len(val)} caractères)"
              f"{'  ⚠ ARME LE RÉEL' if arme else ''}")

    for cle in inconnus:
        print(f"  ignoré    {'?':9} {cle:32} (ni secret ni variable connue)")

    if args.dry_run:
        print("\n  --dry-run : rien n'a été envoyé.\n")
        return 0
    if not a_pousser:
        print("\n  Rien à pousser.\n")
        return 0

    cle_pub = api(token, "GET", f"/repos/{nom}/actions/secrets/public-key")
    print()
    for genre, cle, val, arme in a_pousser:
        if genre == "secret":
            api(token, "PUT", f"/repos/{nom}/actions/secrets/{cle}",
                {"encrypted_value": chiffrer(val, cle_pub["key"]),
                 "key_id": cle_pub["key_id"]})
        else:
            # Les variables n'ont pas de PUT idempotent : on crée, et si elle
            # existe déjà on la met à jour.
            try:
                api(token, "POST", f"/repos/{nom}/actions/variables",
                    {"name": cle, "value": val})
            except SystemExit:
                api(token, "PATCH", f"/repos/{nom}/actions/variables/{cle}",
                    {"name": cle, "value": val})
        print(f"  posé      {genre:9} {cle}")

    print(f"\n  {len(a_pousser)} entrée(s) posée(s). Aucune valeur n'a été affichée.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
