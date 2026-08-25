"""Rédaction des analyses : moteur de gabarits + couche LLM optionnelle.

Règle non négociable de ce module : **aucun chiffre n'est produit ici**.
Tous les nombres proviennent du moteur d'analyse et sont pré-formatés dans un
dictionnaire de faits. Le LLM reçoit ces faits déjà calculés et n'a le droit
que de les mettre en phrases. C'est ce qui rend impossible l'invention d'un
niveau de stop ou d'un pourcentage de performance.

Si aucune clé API n'est configurée, ou si l'appel échoue, le gabarit
déterministe prend le relais sans que rien ne casse.
"""
from __future__ import annotations

import logging
import os

from .config import SETTINGS, _env

log = logging.getLogger("jimbot.narrator")

# Même précaution que dans config : une variable exportée vide par
# GitHub Actions ne doit pas écraser le défaut.
MODEL = _env("JIMBOT_LLM_MODEL", "claude-opus-5")

SYSTEM = """Tu es l'analyste de marché de Jimbot. Tu rédiges en français, pour \
un lecteur qui connaît le trading.

RÈGLES ABSOLUES :
- N'invente JAMAIS un chiffre. Tu ne peux utiliser que les valeurs numériques \
présentes dans les données fournies. Si une donnée manque, écris-le explicitement \
au lieu de l'estimer.
- Ne donne jamais de conseil d'investissement personnalisé et n'emploie pas de \
formulations promettant un gain ("va monter", "profit garanti"). Décris ce que \
montrent les données et ce qui invaliderait la lecture.
- Mentionne systématiquement ce qui pourrait faire échouer le scénario.
- Ton sobre et direct. Pas d'emphase commerciale, pas d'emoji, pas de \
superlatifs. Des phrases courtes.
- N'ajoute ni titre, ni conclusion générique, ni formule d'ouverture : \
uniquement le corps du texte demandé."""

DISCLAIMER = ("Analyse automatisée à but informatif. Ce n'est pas un conseil "
              "en investissement. Les performances passées ne préjugent pas "
              "des performances futures.")


# --------------------------------------------------------------------------
# Faits : la seule source de nombres
# --------------------------------------------------------------------------
def signal_facts(sig: dict) -> dict:
    """Extrait et pré-formate les faits chiffrés d'un signal.

    Le formatage des nombres est fait ici, en Python, pour que le LLM n'ait
    jamais à manipuler un float.
    """
    price = sig["price"]
    digits = _digits(price)
    top = sorted(sig["factors"], key=lambda f: abs(f["contribution"]), reverse=True)[:3]

    facts = {
        "actif": f"{sig['label']} ({sig['symbol']})",
        "classe": sig["klass"],
        "direction": sig["direction"],
        "score": f"{sig['score']:.0f}/100",
        "prix": f"{price:,.{digits}f}",
        "unite_de_temps": sig["timeframe"],
        "regime": sig["regime"]["name"].replace("_", " "),
        "qualite_tendance": f"R²={sig['regime']['quality']:.2f}",
        "hurst": f"{sig['regime']['hurst']:.2f}" if sig["regime"].get("hurst") == sig["regime"].get("hurst") else "indisponible",
        "volatilite_atr": f"{sig['atr_pct']:.2f} % du prix",
        "percentile_volatilite": f"{sig['regime']['vol_percentile'] * 100:.0f}ᵉ percentile",
        "facteurs_dominants": [
            f"{f['name']} (contribution {f['contribution']:+.3f}) : {f['detail']}"
            for f in top
        ],
        "sentiment_presse": (f"{sig['news_score']:+.2f} sur {sig['news_count']} article(s)"
                             if sig["news_count"] else "aucune actualité rattachée"),
        "alignement_unite_superieure": f"{sig['htf_alignment']:+.2f}",
        "avertissements": sig.get("warnings", []),
    }
    if sig["direction"] != "neutre" and sig["stop"] > 0:
        risk_pct = abs(sig["entry"] - sig["stop"]) / sig["entry"] * 100
        facts |= {
            "entree": f"{sig['entry']:,.{digits}f}",
            "stop": f"{sig['stop']:,.{digits}f}",
            "objectif": f"{sig['target']:,.{digits}f}",
            "ratio_rendement_risque": f"{sig['rr']:.2f}",
            "distance_au_stop": f"{risk_pct:.2f} %",
        }
    return facts


def portfolio_facts(perf: dict, portfolio: dict) -> dict:
    """Faits chiffrés du portefeuille papier."""
    if perf.get("trades", 0) == 0:
        return {"etat": "aucun trade fermé pour l'instant",
                "capital": f"{portfolio.get('equity', 0):,.2f}",
                "positions_ouvertes": len(portfolio.get("positions", []))}
    return {
        "capital_actuel": f"{portfolio.get('equity', 0):,.2f}",
        "capital_initial": f"{portfolio.get('initial', 0):,.2f}",
        "rendement_total": f"{perf['total_return_pct']:+.2f} %",
        "trades_fermes": perf["trades"],
        "taux_de_reussite": f"{perf['win_rate']:.1f} %",
        "facteur_de_profit": (f"{perf['profit_factor']:.2f}"
                              if perf.get("profit_factor") else "indéfini (aucune perte)"),
        "esperance_par_trade": f"{perf['expectancy_r']:+.3f} R",
        "drawdown_max": f"{perf['max_drawdown_pct']:.2f} %",
        "gain_moyen": f"{perf['avg_win']:,.2f}",
        "perte_moyenne": f"{perf['avg_loss']:,.2f}",
        "frais_cumules": f"{perf['total_fees']:,.2f}",
        "serie_perdante_max": perf.get("max_loss_streak", 0),
        "positions_ouvertes": len(portfolio.get("positions", [])),
        "repartition_par_regime": perf.get("by_regime", {}),
    }


# --------------------------------------------------------------------------
# Gabarits déterministes (toujours disponibles)
# --------------------------------------------------------------------------
def template_signal(sig: dict) -> str:
    """Rédaction par règles d'un signal. Aucun appel réseau."""
    f = signal_facts(sig)
    sens = {"long": "à l'achat", "short": "à la vente", "neutre": "sans direction"}[sig["direction"]]
    force = ("très forte" if sig["score"] >= 80 else
             "forte" if sig["score"] >= 70 else "modérée")

    lines = [
        f"{f['actif']} ressort {sens} avec une conviction {force} "
        f"({f['score']}) en unité de temps {f['unite_de_temps']}. "
        f"Le marché est en régime « {f['regime']} » ({f['qualite_tendance']}), "
        f"avec une volatilité de {f['volatilite_atr']}."
    ]
    lines.append("Ce qui porte le signal : " + " ; ".join(
        d.split(" : ", 1)[1] if " : " in d else d for d in f["facteurs_dominants"]) + ".")

    if "stop" in f:
        lines.append(
            f"Le plan : entrée vers {f['entree']}, invalidation à {f['stop']} "
            f"(soit {f['distance_au_stop']} de risque), objectif {f['objectif']} "
            f"pour un ratio rendement/risque de {f['ratio_rendement_risque']}. "
            f"Le stop est calé sur la volatilité réelle de l'actif, pas sur un "
            f"pourcentage arbitraire."
        )
    if sig["news_count"]:
        lines.append(f"Côté actualité : sentiment {f['sentiment_presse']}.")
    if f["avertissements"]:
        lines.append("Réserves : " + " ; ".join(f["avertissements"]) + ".")
    else:
        lines.append("Le scénario est invalidé si le prix clôture au-delà du stop, "
                     "ou si le régime de marché bascule.")
    return "\n\n".join(lines)


def template_briefing(facts: dict, signals: list[dict], regimes: dict,
                      risk_off: dict | None = None) -> str:
    """Briefing quotidien par règles."""
    longs = [s for s in signals if s["direction"] == "long"]
    shorts = [s for s in signals if s["direction"] == "short"]
    parts = []

    if signals:
        top = max(signals, key=lambda s: s["score"])
        parts.append(
            f"Le scan retient {len(signals)} configuration(s) : {len(longs)} à l'achat "
            f"et {len(shorts)} à la vente. La plus forte conviction est "
            f"{top['label']} {top['direction']} à {top['score']:.0f}/100."
        )
    else:
        parts.append("Aucune configuration ne dépasse le seuil de conviction. "
                     "Le moteur reste à l'écart : l'absence de signal est un signal.")

    if regimes:
        dominant = max(regimes.items(), key=lambda kv: kv[1])
        parts.append(f"Sur l'univers suivi, le régime dominant est « "
                     f"{dominant[0].replace('_', ' ')} » ({dominant[1]} actifs).")

    niveau = (risk_off or {}).get("level", 0.0)
    if (risk_off or {}).get("count"):
        if niveau > 0.25:
            climat = ("Le climat géopolitique est tendu : les valeurs refuges "
                      "(or, dollar, volatilité) en bénéficient, les indices et "
                      "la crypto en pâtissent.")
        elif niveau < -0.25:
            climat = ("Le climat géopolitique se détend, ce qui favorise les "
                      "actifs de risque au détriment des valeurs refuges.")
        else:
            climat = "Le climat géopolitique est neutre, sans biais directionnel marqué."
        parts.append(f"{climat} Indice de tension : {niveau:+.2f} sur "
                     f"{risk_off['count']} article(s) porteurs.")

    if facts.get("trades_fermes"):
        parts.append(
            f"Le portefeuille papier affiche {facts['rendement_total']} depuis le "
            f"lancement, sur {facts['trades_fermes']} trades fermés, avec un taux de "
            f"réussite de {facts['taux_de_reussite']} et une espérance de "
            f"{facts['esperance_par_trade']} par trade. Drawdown maximal : "
            f"{facts['drawdown_max']}."
        )
    else:
        parts.append(f"Le portefeuille papier n'a encore fermé aucun trade "
                     f"({facts.get('positions_ouvertes', 0)} position(s) ouverte(s)).")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Couche LLM
# --------------------------------------------------------------------------
def _client():
    """Instancie le client Anthropic, ou None si indisponible."""
    if not SETTINGS.anthropic_key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("paquet anthropic absent, rédaction par gabarit")
        return None
    try:
        return anthropic.Anthropic(api_key=SETTINGS.anthropic_key, timeout=90.0)
    except Exception as e:  # noqa: BLE001
        log.warning("client Anthropic indisponible (%s), rédaction par gabarit", e)
        return None


def _ask(prompt: str, *, max_tokens: int = 4000) -> str | None:
    """Un appel au modèle. Renvoie None en cas d'échec, jamais une exception."""
    client = _client()
    if client is None:
        return None
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:  # noqa: BLE001 — la narration ne doit jamais bloquer le scan
        log.warning("appel LLM échoué (%s), repli sur le gabarit", e)
        return None

    if getattr(msg, "stop_reason", None) == "refusal":
        log.warning("réponse refusée par le modèle, repli sur le gabarit")
        return None
    text = "\n".join(b.text for b in msg.content if b.type == "text").strip()
    return text or None


def _facts_block(facts: dict) -> str:
    """Sérialise les faits sous une forme que le modèle ne peut que recopier."""
    import json
    return json.dumps(facts, ensure_ascii=False, indent=2)


def narrate_signal(sig: dict) -> tuple[str, str]:
    """Rédige l'analyse d'un signal. Renvoie (texte, moteur utilisé)."""
    facts = signal_facts(sig)
    prompt = (
        "Rédige l'analyse de ce signal de trading en 3 paragraphes courts :\n"
        "1) ce que montre la configuration technique et le régime de marché ;\n"
        "2) le plan de trade et la logique du niveau d'invalidation ;\n"
        "3) ce qui rendrait cette lecture fausse.\n\n"
        "Utilise exclusivement les valeurs ci-dessous, sans en inventer ni en "
        "arrondir différemment.\n\nDONNÉES CALCULÉES :\n" + _facts_block(facts)
    )
    text = _ask(prompt, max_tokens=2000)
    return (text, "llm") if text else (template_signal(sig), "gabarit")


def narrate_briefing(perf: dict, portfolio: dict, signals: list[dict],
                     regimes: dict, top_news: list[dict],
                     risk_off: dict | None = None) -> tuple[str, str]:
    """Rédige le discours de marché quotidien. Renvoie (texte, moteur utilisé)."""
    facts = portfolio_facts(perf, portfolio)
    signal_summary = [
        {"actif": s["label"], "sens": s["direction"], "score": round(s["score"]),
         "regime": s["regime"]["name"], "unite_de_temps": s["timeframe"]}
        for s in sorted(signals, key=lambda x: -x["score"])[:8]
    ]
    news_summary = [{"titre": n["title"], "source": n["source"],
                     "sentiment": n["sentiment"]} for n in top_news[:8]]

    risk_off = risk_off or {}
    climat = {
        "tension_mondiale": f"{risk_off.get('level', 0.0):+.2f} sur une échelle de -1 (apaisement) à +1 (escalade)",
        "articles_geopolitiques": risk_off.get("count", 0),
        "faits_marquants": [f"{t['title']} ({t['source']}, tension {t['risk']:+.1f})"
                            for t in risk_off.get("top", [])[:5]],
    }
    prompt = (
        "Rédige le briefing de marché quotidien en 5 à 7 paragraphes :\n"
        "1) l'état général des marchés suivis et le régime dominant ;\n"
        "2) les configurations retenues et pourquoi ;\n"
        "3) le contexte géopolitique mondial et son effet attendu : une escalade "
        "profite aux valeurs refuges (or, dollar, volatilité) et pèse sur les "
        "actifs de risque (indices, crypto) ;\n"
        "4) ce que dit l'actualité de marché et si elle confirme ou contredit "
        "la technique ;\n"
        "5) l'état du portefeuille papier, lu honnêtement (si les résultats sont "
        "mauvais, dis-le clairement) ;\n"
        "6) ce qu'il faut surveiller ensuite.\n\n"
        "Utilise exclusivement les valeurs ci-dessous.\n\n"
        "PORTEFEUILLE ET PERFORMANCE :\n" + _facts_block(facts) +
        "\n\nRÉGIMES DE MARCHÉ (nombre d'actifs par régime) :\n" + _facts_block(regimes) +
        "\n\nSIGNAUX RETENUS :\n" + _facts_block(signal_summary) +
        "\n\nCLIMAT GÉOPOLITIQUE :\n" + _facts_block(climat) +
        "\n\nACTUALITÉS MARQUANTES :\n" + _facts_block(news_summary)
    )
    text = _ask(prompt, max_tokens=6000)
    return (text, "llm") if text else (template_briefing(facts, signals, regimes, risk_off), "gabarit")


def _digits(price: float) -> int:
    """Nombre de décimales adapté à l'ordre de grandeur du prix.

    Un memecoin à 0.0000034 et un indice à 7652 ne se formatent pas pareil.
    """
    if price >= 1000:
        return 2
    if price >= 1:
        return 4
    if price >= 0.01:
        return 6
    return 10


# --------------------------------------------------------------------------
# Résumé d'actualité
# --------------------------------------------------------------------------
def news_facts(articles: list[dict], sentiment: dict, risk_off: dict,
               speeches: list[dict], agenda: dict | None = None) -> dict:
    """Agrège l'actualité en faits chiffrés, prêts à être mis en phrases.

    Un tableau de titres n'est pas un résumé : il demande au lecteur de faire
    lui-même la synthèse. On regroupe donc par thème, on classe par impact, et
    on rattache chaque bloc aux actifs concernés.
    """
    marches = [a for a in articles if a.get("category") == "marches"]
    monde = [a for a in articles if a.get("category") == "monde"]

    def top(items: list[dict], cle, n: int = 5) -> list[dict]:
        return sorted(items, key=cle, reverse=True)[:n]

    # Actifs les plus cités, avec le sens du sentiment agrégé.
    par_actif = sorted(
        ((sym, v) for sym, v in sentiment.items() if v.get("count", 0) > 0),
        key=lambda kv: -kv[1]["count"])[:6]

    return {
        "articles_total": len(articles),
        "articles_marches": len(marches),
        "articles_monde": len(monde),
        "tension_geopolitique": f"{risk_off.get('level', 0.0):+.2f} sur [-1, +1]",
        "articles_geopolitiques": risk_off.get("count", 0),
        "plus_marquants": [
            {"titre": a["title"], "source": a["source"],
             "sentiment": a["sentiment"], "actifs": a.get("assets", [])}
            for a in top(marches, lambda x: abs(x.get("sentiment", 0)))
        ],
        "faits_geopolitiques": [
            {"titre": t["title"], "source": t["source"], "tension": t["risk"],
             "termes": t.get("terms", [])}
            for t in risk_off.get("top", [])[:5]
        ],
        "discours": [
            {"orateur": s["speaker"], "propos": s["title"], "tonalite": s["tone"],
             "effet_or": s["impact"].get("XAUUSD", 0.0)}
            for s in speeches[:3]
        ],
        "echeances_mecaniques": [
            {"dans_jours": e["days_ahead"], "libelle": e["label"],
             "impact": e["impact"]}
            for e in (agenda or {}).get("mechanical", [])[:5]
        ],
        "echeances_annoncees_par_la_presse": [
            {"libelle": e["label"], "impact": e["impact"], "source": e["source"],
             "titre": e["detail"]}
            for e in (agenda or {}).get("press", [])[:5]
        ],
        "actifs_les_plus_cites": [
            {"actif": sym, "articles": v["count"],
             "sentiment": v["score"],
             "part_geopolitique": v.get("geo", 0.0),
             "part_monetaire": v.get("monetary", 0.0)}
            for sym, v in par_actif
        ],
    }


def template_news(facts: dict) -> str:
    """Résumé d'actualité par règles. Aucun appel réseau."""
    parts: list[str] = []

    parts.append(
        f"{facts['articles_total']} articles retenus sur le cycle, dont "
        f"{facts['articles_monde']} d'actualité internationale et "
        f"{facts['articles_marches']} d'actualité de marché.")

    if facts["faits_geopolitiques"]:
        titres = " ; ".join(f["titre"][:90] for f in facts["faits_geopolitiques"][:3])
        parts.append(
            f"Sur le plan géopolitique, l'indice de tension s'établit à "
            f"{facts['tension_geopolitique']} sur "
            f"{facts['articles_geopolitiques']} articles porteurs. Les faits "
            f"dominants : {titres}.")

    if facts["discours"]:
        d = facts["discours"][0]
        sens = "accommodante" if d["tonalite"] > 0 else "restrictive"
        parts.append(
            f"Côté politique monétaire, la prise de parole la plus marquante est "
            f"celle de {d['orateur'].title()}, de tonalité {sens} "
            f"({d['tonalite']:+.1f}), avec un effet attendu de "
            f"{d['effet_or']:+.2f} sur l'or.")

    if facts["plus_marquants"]:
        a = facts["plus_marquants"][0]
        sens = "positive" if a["sentiment"] > 0 else "négative"
        parts.append(
            f"L'information de marché la plus chargée est {sens} : "
            f"« {a['titre'][:110]} » ({a['source']}, {a['sentiment']:+.1f}).")

    mech = facts.get("echeances_mecaniques", [])
    presse = facts.get("echeances_annoncees_par_la_presse", [])
    if mech or presse:
        bouts = []
        for e in mech[:3]:
            quand = "aujourd'hui" if e["dans_jours"] == 0 else f"dans {e['dans_jours']} j"
            bouts.append(f"{e['libelle']} ({quand})")
        for e in presse[:3]:
            bouts.append(f"{e['libelle']} (annoncé par {e['source']})")
        parts.append("À venir : " + " ; ".join(bouts) + ".")

    if facts["actifs_les_plus_cites"]:
        listing = ", ".join(
            f"{x['actif']} ({x['articles']} art., {x['sentiment']:+.2f})"
            for x in facts["actifs_les_plus_cites"][:4])
        parts.append(f"Actifs les plus couverts : {listing}.")

    return "\n\n".join(parts)


def narrate_news(articles: list[dict], sentiment: dict, risk_off: dict,
                 speeches: list[dict], agenda: dict | None = None) -> tuple[str, str]:
    """Rédige le résumé d'actualité. Renvoie (texte, moteur utilisé)."""
    facts = news_facts(articles, sentiment, risk_off, speeches, agenda)
    prompt = (
        "Rédige un résumé d'actualité en 4 paragraphes courts, destiné à un "
        "lecteur qui veut comprendre le contexte avant de regarder les "
        "graphiques :\n"
        "1) ce qui domine l'actualité internationale et la tension qui en découle ;\n"
        "2) ce que disent les banques centrales et l'actualité de marché ;\n"
        "3) quels actifs sont concernés et dans quel sens ;\n"
        "4) ce qui arrive dans les prochains jours et ce qu'il faut surveiller. "
        "Pour les échéances annoncées par la presse, ne donne aucune date "
        "précise : seule la source la connaît, cite-la sans l'inventer.\n\n"
        "Rappelle que l'effet d'une escalade dépend de l'actif : haussier pour "
        "l'or, le dollar et la volatilité, baissier pour les indices et la "
        "crypto. Utilise exclusivement les valeurs ci-dessous.\n\n"
        "DONNÉES CALCULÉES :\n" + _facts_block(facts)
    )
    text = _ask(prompt, max_tokens=3000)
    return (text, "llm") if text else (template_news(facts), "gabarit")
