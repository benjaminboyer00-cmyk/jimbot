#!/usr/bin/env python3
"""Rétro-remplissage de l'historique depuis git.

Le moteur committe `data/latest.json` à chaque scan depuis le premier jour.
Chaque révision de ce fichier est donc une photo horodatée et immuable de
l'univers : le prix de chaque actif, son score, son régime. L'historique des
scans existait déjà — il était simplement enfermé dans l'historique de git,
d'où le dashboard ne peut pas le lire.

Ce script l'en sort. Il parcourt toutes les révisions de `data/latest.json`,
en extrait un point par actif et par scan, et les fusionne dans
`data/history.json`. Les points déjà présents ne sont pas dupliqués : on peut
donc le relancer autant de fois que nécessaire.

L'opération est à faire **une fois**. Ensuite, chaque scan entretient
l'historique de lui-même (voir `jimbot/history.py`).

Usage :
    python engine/backfill_history.py            # remplit et écrit
    python engine/backfill_history.py --dry-run  # compte, sans écrire
    python engine/backfill_history.py --limit 50 # n'examine que N révisions
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from jimbot import history  # noqa: E402
from jimbot.config import ROOT  # noqa: E402
from jimbot.store import write_raw  # noqa: E402

CHEMIN = "data/latest.json"

log = logging.getLogger("jimbot.backfill")


def revisions(limite: int | None = None) -> list[str]:
    """Révisions de `data/latest.json`, de la plus ancienne à la plus récente."""
    out = subprocess.run(
        ["git", "log", "--format=%H", "--reverse", "--", CHEMIN],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    shas = [ligne for ligne in out.stdout.split("\n") if ligne]
    return shas[-limite:] if limite else shas


def instantane(sha: str) -> dict | None:
    """Contenu de `data/latest.json` à une révision donnée."""
    out = subprocess.run(
        ["git", "show", f"{sha}:{CHEMIN}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        # Un commit interrompu a pu laisser un JSON tronqué. Il n'y a rien à
        # en tirer, et ce n'est pas une raison d'abandonner les 200 autres.
        log.warning("révision %s : JSON illisible, ignorée", sha[:8])
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Rétro-remplissage de l'historique")
    parser.add_argument("--dry-run", action="store_true",
                        help="analyse sans écrire le fichier")
    parser.add_argument("--limit", type=int, default=None,
                        help="ne traiter que les N révisions les plus récentes")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    shas = revisions(args.limit)
    log.info("%d révision(s) de %s à examiner", len(shas), CHEMIN)
    if not shas:
        log.error("aucune révision trouvée — êtes-vous dans le dépôt ?")
        return 1

    hist = history.charger()
    avant = sum(len(a["points"]) for a in hist["actifs"].values())
    lus = ignores = 0
    dernier = hist.get("generated_at")

    for i, sha in enumerate(shas, start=1):
        snap = instantane(sha)
        if not snap:
            ignores += 1
            continue
        horodatage = snap.get("generated_at")
        signaux = snap.get("signals")
        if not horodatage or not isinstance(signaux, list):
            ignores += 1
            continue
        history.fusionner(hist, signaux, horodatage)
        lus += 1
        if dernier is None or horodatage > dernier:
            dernier = horodatage
        if i % 50 == 0:
            log.info("… %d/%d révisions", i, len(shas))

    history.ordonner(hist)
    hist["generated_at"] = dernier
    apres = sum(len(a["points"]) for a in hist["actifs"].values())

    log.info("%d révision(s) exploitée(s), %d ignorée(s)", lus, ignores)
    log.info("%d actif(s) · %d point(s) avant, %d après (+%d)",
             len(hist["actifs"]), avant, apres, apres - avant)
    for sym in sorted(hist["actifs"]):
        pts = hist["actifs"][sym]["points"]
        if pts:
            log.info("  %-10s %4d point(s)  %s → %s",
                     sym, len(pts), pts[0][0][:16], pts[-1][0][:16])

    if args.dry_run:
        log.info("--dry-run : rien n'a été écrit")
        return 0

    texte = history.serialiser(hist)
    write_raw("history", texte)
    log.info("data/history.json écrit (%.0f Ko)", len(texte.encode("utf-8")) / 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
