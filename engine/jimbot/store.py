"""Persistance JSON versionnée dans le dépôt.

Chaque exécution de GitHub Actions écrit ici, puis committe. L'historique de
git devient l'historique du bot : chaque signal est horodaté et immuable, ce
qui rend le suivi de performance non falsifiable a posteriori.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

log = logging.getLogger("jimbot.store")

# Bornes de rétention : les fichiers restent lisibles par le dashboard et
# légers pour git.
MAX_SIGNALS_HISTORY = 2000
MAX_CLOSED_TRADES = 1000
MAX_ALERTS = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def read(name: str, default: Any = None) -> Any:
    """Lit un fichier de données, tolérant à l'absence et à la corruption."""
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("fichier %s illisible (%s), valeur par défaut utilisée", p.name, e)
        return default


def write(name: str, payload: Any) -> Path:
    """Écrit de façon atomique : pas de JSON tronqué si le job est tué.

    Un fichier partiel serait committé par Actions et casserait le dashboard ;
    l'écriture passe donc par un temporaire puis un rename atomique.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(name)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp crée en 0600 et os.replace conserve les droits : sans cette
        # ligne, les fichiers de données ne seraient lisibles que par le
        # compte qui a lancé le scan.
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    log.debug("écrit %s (%.1f Ko)", p.name, p.stat().st_size / 1024)
    return p


def append_history(name: str, items: list[dict], cap: int) -> list[dict]:
    """Ajoute des entrées à un historique borné, les plus récentes en tête."""
    history = read(name, []) or []
    if not isinstance(history, list):
        log.warning("historique %s corrompu, réinitialisé", name)
        history = []
    merged = items + history
    trimmed = merged[:cap]
    write(name, trimmed)
    return trimmed
