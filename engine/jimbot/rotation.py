"""Rotation sectorielle : où l'argent va, plutôt que si le marché monte.

Un indice d'ensemble cache ce qui l'intéresse. Le S&P 500 peut finir plat
pendant que l'énergie parcourt deux fois sa journée ordinaire et que la
technologie rend tout ce qu'elle avait pris : la moyenne dit « rien ne se
passe » là où deux secteurs se sont échangé des capitaux.

Ce module ne prédit rien et n'entre dans aucun score. Il classe les secteurs
sur ce que `strategy.mouvement` a déjà mesuré — le parcours accompli rapporté
à la journée ordinaire de chacun — et rattache à chaque secteur les titres
suivis qui en relèvent.

Le rapport à la journée ordinaire n'est pas un détail de présentation : c'est
ce qui rend l'énergie et la technologie comparables, alors qu'elles ne bougent
pas des mêmes pourcentages. Sans lui, le classement mesurerait surtout quel
secteur est structurellement le plus volatil, ce qu'on sait déjà.
"""
from __future__ import annotations

from .config import SECTEUR_DE

# Un secteur est retenu comme « aimant » quand il a parcouru sensiblement plus
# que sa journée ordinaire *et* qu'il en a gardé quelque chose. Les deux
# conditions comptent : un parcours ample sans rétention est une secousse, et
# elle ne dit pas où va l'argent.
SEUIL_AMPLEUR = 1.3
SEUIL_RETENTION = 0.45


def _mouvement(signal: dict) -> dict | None:
    m = signal.get("mouvement")
    return m if isinstance(m, dict) and m.get("disponible") else None


def classer(signaux: list[dict]) -> dict:
    """Classe les secteurs par intensité de mouvement, titres rattachés.

    Renvoie une structure directement affichable, sans calcul côté site : le
    classement doit être le même pour le rapport PDF, l'API et le tableau de
    bord, et trois implémentations auraient divergé.
    """
    par_symbole = {s.get("symbol"): s for s in signaux}

    secteurs = []
    for s in signaux:
        if s.get("klass") != "secteur":
            continue
        m = _mouvement(s)
        if not m:
            continue

        # Les titres suivis qui relèvent de ce secteur, classés comme lui.
        titres = []
        for sym, sect in SECTEUR_DE.items():
            if sect != s["symbol"] or sym not in par_symbole:
                continue
            t = par_symbole[sym]
            mt = _mouvement(t)
            if not mt:
                continue
            titres.append({
                "symbol": sym,
                "label": t.get("label", sym),
                "var_24h": mt["var_24h"],
                "ampleur": mt["ampleur"],
                "retention": mt["retention"],
                "etat": mt["etat"],
                "score": t.get("score", 0.0),
                "biais": t.get("bias", "neutre"),
            })
        titres.sort(key=lambda x: -x["ampleur"])

        # Un titre qui bouge beaucoup plus que son secteur porte le mouvement
        # au lieu de le suivre : c'est ce que « ce qui perce » désigne.
        perce = [t for t in titres if t["ampleur"] > m["ampleur"] * 1.3]

        secteurs.append({
            "symbol": s["symbol"],
            "label": s.get("label", s["symbol"]),
            "var_24h": m["var_24h"],
            "var_7j": m.get("var_7j"),
            "ampleur": m["ampleur"],
            "retention": m["retention"],
            "etat": m["etat"],
            "aimant": bool(m["ampleur"] >= SEUIL_AMPLEUR
                           and m["retention"] >= SEUIL_RETENTION),
            "titres": titres,
            "percent": [t["symbol"] for t in perce],
        })

    secteurs.sort(key=lambda x: -x["ampleur"])

    aimants = [s for s in secteurs if s["aimant"]]
    return {
        "seuils": {"ampleur": SEUIL_AMPLEUR, "retention": SEUIL_RETENTION},
        "secteurs": secteurs,
        # Les secteurs qui attirent, dans l'ordre : hausse d'abord, baisse
        # ensuite, parce qu'un secteur qu'on fuit est aussi une destination.
        "aimants": [s["symbol"] for s in aimants if s["var_24h"] > 0],
        "delaisses": [s["symbol"] for s in aimants if s["var_24h"] <= 0],
        # Comptés séparément : la dispersion dit s'il y a rotation ou non. Tous
        # les secteurs qui montent ensemble, ce n'est pas une rotation, c'est
        # une marée.
        "dispersion": round(
            (max((s["ampleur"] for s in secteurs), default=0.0)
             - min((s["ampleur"] for s in secteurs), default=0.0)), 2),
    }
