"""Publication Discord via webhook : alertes, briefings, rapports.

Un webhook suffit — pas de bot persistant, donc rien à héberger. Les limites
de l'API Discord sont strictement respectées (2000 caractères par message,
4096 par description d'embed, 10 embeds par envoi), car un dépassement
renvoie un 400 et fait perdre l'alerte silencieusement.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import SETTINGS
from .store import now_iso, read, write

log = logging.getLogger("jimbot.discord")

# Limites imposées par l'API Discord.
MAX_CONTENT = 2000
MAX_EMBED_DESC = 4096
MAX_EMBEDS = 10
MAX_FIELD_VALUE = 1024

# Couleurs sobres, lisibles en thème clair comme sombre.
COLOR_LONG = 0x2E7D5B     # vert profond
COLOR_SHORT = 0x9B3A3A    # rouge brique
COLOR_NEUTRAL = 0x4A5568  # ardoise
COLOR_INFO = 0x3D5A80     # bleu ardoise


class DiscordError(RuntimeError):
    pass


def _post(payload: dict, *, files: dict | None = None, retries: int = 3) -> bool:
    """Envoie au webhook, en respectant le rate-limit renvoyé par Discord."""
    if SETTINGS.dry_run:
        log.info("[DRY RUN] envoi Discord simulé : %s",
                 (payload.get("content") or "")[:120] or f"{len(payload.get('embeds', []))} embed(s)")
        return True
    if not SETTINGS.discord_webhook:
        log.warning("DISCORD_WEBHOOK_URL non configuré, publication ignorée")
        return False

    for attempt in range(retries):
        try:
            if files:
                # multipart : le JSON passe dans le champ payload_json.
                import json
                resp = requests.post(SETTINGS.discord_webhook,
                                     data={"payload_json": json.dumps(payload)},
                                     files=files, timeout=30)
            else:
                resp = requests.post(SETTINGS.discord_webhook, json=payload, timeout=20)

            if resp.status_code == 429:
                wait = float(resp.json().get("retry_after", 2.0))
                log.warning("rate-limit Discord, attente de %.1f s", wait)
                time.sleep(min(wait + 0.5, 30))
                continue
            if resp.status_code >= 400:
                log.error("Discord a répondu %s : %s", resp.status_code, resp.text[:300])
                if resp.status_code < 500:
                    return False  # erreur de notre côté : réessayer ne sert à rien
                time.sleep(2 ** attempt)
                continue
            return True
        except requests.RequestException as e:
            log.warning("envoi Discord échoué (tentative %d) : %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _fmt(value: float, digits: int | None = None) -> str:
    if digits is None:
        digits = 2 if abs(value) >= 1000 else 4 if abs(value) >= 1 else 8
    return f"{value:,.{digits}f}"


# --------------------------------------------------------------------------
# Anti-spam
# --------------------------------------------------------------------------
def should_alert(symbol: str, direction: str) -> bool:
    """Empêche de republier le même appel toutes les 15 minutes.

    Sans ce garde-fou, un signal qui reste au-dessus du seuil pendant six
    heures produirait 24 alertes identiques et le salon deviendrait inutile.
    """
    sent = read("alerts_sent", {}) or {}
    key = f"{symbol}:{direction}"
    last = sent.get(key)
    if not last:
        return True
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
    except ValueError:
        return True
    if elapsed < SETTINGS.alert_cooldown_min:
        log.info("%s : alerte ignorée, dernière il y a %.0f min (délai %d min)",
                 key, elapsed, SETTINGS.alert_cooldown_min)
        return False
    return True


def mark_alerted(symbol: str, direction: str) -> None:
    sent = read("alerts_sent", {}) or {}
    sent[f"{symbol}:{direction}"] = now_iso()
    # Purge les entrées trop vieilles pour rester pertinentes.
    cutoff = time.time() - 7 * 86400
    sent = {k: v for k, v in sent.items()
            if _ts(v) > cutoff}
    write("alerts_sent", sent)


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
def signal_embed(sig: dict, narrative: str = "") -> dict:
    """Construit l'embed d'un signal de trading."""
    direction = sig["direction"]
    color = COLOR_LONG if direction == "long" else COLOR_SHORT if direction == "short" else COLOR_NEUTRAL
    arrow = "▲" if direction == "long" else "▼" if direction == "short" else "■"
    sens = {"long": "ACHAT", "short": "VENTE", "neutre": "NEUTRE"}[direction]

    digits = 2 if sig["price"] >= 1000 else 4 if sig["price"] >= 1 else 8
    fields = [
        {"name": "Conviction", "value": f"**{sig['score']:.0f}**/100", "inline": True},
        {"name": "Régime", "value": sig["regime"]["name"].replace("_", " "), "inline": True},
        {"name": "Volatilité", "value": f"{sig['atr_pct']:.2f} % (ATR)", "inline": True},
    ]
    if sig["stop"] > 0:
        fields += [
            {"name": "Entrée", "value": _fmt(sig["entry"], digits), "inline": True},
            {"name": "Invalidation", "value": _fmt(sig["stop"], digits), "inline": True},
            {"name": "Objectif", "value": f"{_fmt(sig['target'], digits)}  ·  R/R {sig['rr']:.1f}", "inline": True},
        ]

    top = sorted(sig["factors"], key=lambda f: abs(f["contribution"]), reverse=True)[:3]
    fields.append({
        "name": "Facteurs dominants",
        "value": _truncate("\n".join(f"• {f['name']} — {f['detail']}" for f in top), MAX_FIELD_VALUE),
        "inline": False,
    })
    if sig["news_count"]:
        fields.append({"name": "Sentiment presse",
                       "value": f"{sig['news_score']:+.2f} sur {sig['news_count']} article(s)",
                       "inline": True})
    if sig.get("warnings"):
        fields.append({"name": "⚠ Réserves",
                       "value": _truncate("\n".join(f"• {w}" for w in sig["warnings"]), MAX_FIELD_VALUE),
                       "inline": False})

    return {
        "title": f"{arrow}  {sens} — {sig['label']} ({sig['symbol']})",
        "description": _truncate(narrative, MAX_EMBED_DESC) if narrative else "",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Jimbot · {sig['timeframe']} · prix {_fmt(sig['price'], digits)} · "
                           f"analyse automatisée, pas un conseil en investissement"},
        "timestamp": sig.get("generated_at") or now_iso(),
    }


def send_signal(sig: dict, narrative: str = "") -> bool:
    """Publie une alerte de signal, avec ping de rôle si la conviction est forte."""
    if not should_alert(sig["symbol"], sig["direction"]):
        return False

    content = ""
    if sig["score"] >= SETTINGS.ping_threshold and SETTINGS.discord_role_id:
        content = f"<@&{SETTINGS.discord_role_id}> conviction {sig['score']:.0f}/100"

    ok = _post({
        "content": content,
        "embeds": [signal_embed(sig, narrative)],
        # Le ping de rôle n'est autorisé que s'il est explicitement déclaré ici.
        "allowed_mentions": {"parse": [], "roles": [SETTINGS.discord_role_id] if content else []},
    })
    if ok:
        mark_alerted(sig["symbol"], sig["direction"])
    return ok


def send_briefing(text: str, title: str = "Briefing de marché",
                  stats: dict | None = None) -> bool:
    """Publie un texte long, découpé proprement pour respecter les limites."""
    chunks = _split(text, MAX_EMBED_DESC)
    embeds = []
    for i, chunk in enumerate(chunks[:MAX_EMBEDS]):
        embed = {"description": chunk, "color": COLOR_INFO}
        if i == 0:
            embed["title"] = title
        if i == len(chunks) - 1:
            embed["footer"] = {"text": "Jimbot · analyse automatisée, pas un conseil en investissement"}
            embed["timestamp"] = now_iso()
        embeds.append(embed)

    if stats:
        embeds[-1]["fields"] = [{"name": k, "value": str(v), "inline": True}
                                for k, v in list(stats.items())[:9]]
    return _post({"embeds": embeds, "allowed_mentions": {"parse": []}})


def send_report(pdf_path: Path, summary: str = "", stats: dict | None = None) -> bool:
    """Publie le rapport PDF en pièce jointe."""
    p = Path(pdf_path)
    if not p.exists():
        log.error("rapport introuvable : %s", p)
        return False
    size_mb = p.stat().st_size / 1_048_576
    if size_mb > 8:
        log.error("rapport trop lourd pour Discord (%.1f Mo)", size_mb)
        return False

    embed = {
        "title": f"Rapport quotidien — {datetime.now(timezone.utc):%d/%m/%Y}",
        "description": _truncate(summary, MAX_EMBED_DESC),
        "color": COLOR_INFO,
        "footer": {"text": "Jimbot · analyse automatisée, pas un conseil en investissement"},
        "timestamp": now_iso(),
    }
    if stats:
        embed["fields"] = [{"name": k, "value": str(v), "inline": True}
                           for k, v in list(stats.items())[:9]]

    with p.open("rb") as fh:
        return _post({"embeds": [embed], "allowed_mentions": {"parse": []}},
                     files={"file": (p.name, fh, "application/pdf")})


def _split(text: str, limit: int) -> list[str]:
    """Découpe un texte long sans couper au milieu d'un paragraphe."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # Un paragraphe seul plus long que la limite est coupé par phrases.
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        current = para
    if current:
        chunks.append(current)
    return chunks
