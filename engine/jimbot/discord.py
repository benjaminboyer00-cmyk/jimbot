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
    """Enregistre l'envoi pour le délai anti-spam.

    Une simulation ne marque rien : sinon un test en `--dry-run` consommerait
    le budget anti-spam réel, et l'état — étant committé — empêcherait la
    véritable exécution de publier l'alerte. C'est exactement ce qui s'est
    produit : un essai local a bloqué la première alerte géopolitique en
    production pendant six heures.
    """
    if SETTINGS.dry_run:
        log.debug("[DRY RUN] délai anti-spam non consommé pour %s:%s", symbol, direction)
        return
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


# --------------------------------------------------------------------------
# Alertes de contexte : discours et géopolitique
# --------------------------------------------------------------------------
COLOR_GOLD = 0x8A6D2F     # or mat, pour les alertes de politique monétaire
COLOR_ALERT = 0x8B5A2B    # terre de Sienne, pour la géopolitique


def should_alert_context(key: str, cooldown_min: int = 240) -> bool:
    """Anti-doublon des alertes de contexte, indépendant des alertes de signal.

    Un même discours est repris par plusieurs médias pendant des heures : sans
    ce garde-fou, le salon recevrait la même alerte une dizaine de fois.
    """
    sent = read("context_sent", {}) or {}
    last = sent.get(key)
    if not last:
        return True
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
    except ValueError:
        return True
    return elapsed >= cooldown_min


def mark_context_alerted(key: str) -> None:
    """Idem pour les alertes de contexte : une simulation ne consomme rien."""
    if SETTINGS.dry_run:
        log.debug("[DRY RUN] délai anti-spam non consommé pour %s", key)
        return
    sent = read("context_sent", {}) or {}
    sent[key] = now_iso()
    cutoff = time.time() - 7 * 86400
    write("context_sent", {k: v for k, v in sent.items() if _ts(v) > cutoff})


def send_speech_alert(speech: dict) -> bool:
    """Publie une alerte sur un discours de politique monétaire.

    L'or est mis en avant parce que c'est l'actif dont la réaction à la
    rhétorique monétaire est la plus directe et la plus fiable : il ne dépend
    d'aucun bénéfice, seulement des taux réels et de la confiance dans la
    monnaie.
    """
    # Clé stable : le même orateur avec la même tonalité ne réalerte pas.
    key = f"discours:{speech['speaker']}:{'accommodant' if speech['tone'] > 0 else 'restrictif'}"
    if not should_alert_context(key):
        log.info("%s : alerte de discours ignorée (doublon récent)", key)
        return False

    accommodant = speech["tone"] > 0
    ton = "ACCOMMODANT" if accommodant else "RESTRICTIF"
    sens_or = "haussier" if accommodant else "baissier"

    impacts = speech.get("impact", {})
    lignes = []
    for sym, eff in sorted(impacts.items(), key=lambda kv: -abs(kv[1]))[:6]:
        fleche = "▲" if eff > 0 else "▼" if eff < 0 else "■"
        lignes.append(f"{fleche} `{sym:<8}` {eff:+.2f}")

    embed = {
        "title": f"◆ Discours {ton} — {speech['speaker'].title()}",
        "description": _truncate(speech["title"], MAX_EMBED_DESC),
        "color": COLOR_GOLD,
        "fields": [
            {"name": "Tonalité monétaire", "value": f"**{speech['tone']:+.1f}**", "inline": True},
            {"name": "Importance", "value": f"{speech['importance']:.2f}/1.00", "inline": True},
            {"name": "Ancienneté", "value": f"{speech['age_hours']:.0f} h", "inline": True},
            {"name": "Termes relevés",
             "value": _truncate(", ".join(speech["terms"]) or "—", MAX_FIELD_VALUE),
             "inline": False},
            {"name": "Effet attendu par actif (sensibilité aux taux)",
             "value": _truncate("\n".join(lignes) or "—", MAX_FIELD_VALUE),
             "inline": False},
            {"name": "Or (XAUUSD)",
             "value": f"Biais **{sens_or}** — l'or est l'actif le plus directement "
                      f"lié aux taux réels.",
             "inline": False},
        ],
        "footer": {"text": "Jimbot · effet calculé à partir de la tonalité et de la "
                           "sensibilité aux taux · pas un conseil en investissement"},
        "timestamp": now_iso(),
    }

    content = ""
    if speech["importance"] >= 0.8 and SETTINGS.discord_role_id:
        content = f"<@&{SETTINGS.discord_role_id}> discours à fort impact"

    ok = _post({
        "content": content,
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [SETTINGS.discord_role_id] if content else []},
    })
    if ok:
        mark_context_alerted(key)
    return ok


def send_geopolitical_alert(risk_off: dict, threshold: float = 0.35) -> bool:
    """Publie une alerte quand la tension mondiale franchit un seuil.

    L'intérêt pratique : une escalade fait monter l'or et la volatilité, et
    pèse sur les indices et la crypto. L'alerte donne le sens de la rotation,
    pas une recommandation.
    """
    niveau = risk_off.get("level", 0.0)
    if abs(niveau) < threshold:
        return False

    escalade = niveau > 0
    key = f"geo:{'escalade' if escalade else 'apaisement'}:{round(abs(niveau), 1)}"
    if not should_alert_context(key, cooldown_min=360):
        log.info("%s : alerte géopolitique ignorée (doublon récent)", key)
        return False

    titre = ("▲ Tension géopolitique en hausse" if escalade
             else "▼ Détente géopolitique")
    rotation = ("Rotation vers les valeurs refuges : or, dollar et volatilité "
                "favorisés ; indices et crypto sous pression."
                if escalade else
                "Rotation vers les actifs de risque : indices et crypto favorisés ; "
                "valeurs refuges sous pression.")

    faits = [f"• {t['title'][:110]} — *{t['source']}* ({t['risk']:+.1f})"
             for t in risk_off.get("top", [])[:5]]

    embed = {
        "title": titre,
        "description": rotation,
        "color": COLOR_ALERT,
        "fields": [
            {"name": "Indice de tension", "value": f"**{niveau:+.2f}** sur [-1, +1]", "inline": True},
            {"name": "Articles porteurs", "value": str(risk_off.get("count", 0)), "inline": True},
            {"name": "Faits marquants",
             "value": _truncate("\n".join(faits) or "—", MAX_FIELD_VALUE),
             "inline": False},
        ],
        "footer": {"text": "Jimbot · indice calculé par lexique pondéré · "
                           "pas un conseil en investissement"},
        "timestamp": now_iso(),
    }

    content = ""
    if abs(niveau) >= 0.6 and SETTINGS.discord_role_id:
        content = f"<@&{SETTINGS.discord_role_id}> mouvement géopolitique majeur"

    ok = _post({
        "content": content,
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [SETTINGS.discord_role_id] if content else []},
    })
    if ok:
        mark_context_alerted(key)
    return ok
