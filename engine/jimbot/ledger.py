"""Redevabilité : ce que le marché a fait après chaque signal réellement émis.

Le backtest rejoue des trades que personne n'a vus. Le portefeuille papier
n'ouvre qu'une fraction des signaux, et seulement quand les plafonds de risque
le permettent. Entre les deux, il manquait la seule chose qu'un lecteur puisse
vérifier : **les signaux qui ont effectivement été publiés, et ce qu'ils ont
donné**. C'est ce fichier.

Un signal, pas une émission
---------------------------
Le moteur réémet le même signal à chaque scan tant que la configuration tient.
Vingt-deux émissions d'un achat sur l'or ne sont pas vingt-deux occasions :
c'est une poignée de signaux, chacun répété. Les compter une par une gonflerait
artificiellement l'échantillon et rendrait n'importe quel taux de réussite
insignifiant.

On regroupe donc les émissions consécutives d'un même actif dans le même sens
en un **épisode**, dès lors qu'elles sont espacées de moins que le délai
anti-spam Discord (`alert_cooldown_min`). Le critère n'est pas arbitraire :
deux émissions plus rapprochées que ce délai n'ont pas pu produire deux
alertes distinctes, elles sont donc un seul signal du point de vue de qui lit
le salon. Le plan retenu est celui de la **première** émission — c'est le prix
qu'aurait obtenu quelqu'un qui a agi sur l'alerte.

Comment l'issue est déterminée
------------------------------
Sur les bougies horaires postérieures à l'émission, avec exactement les règles
du backtest (`backtest.simulate_exit`) : le stop prime en cas d'ambiguïté,
l'horizon est borné à `MAX_HOLD` bougies, et les coûts de transaction sont
appliqués dans le sens défavorable aux deux jambes. Un chiffre calculé
autrement ne serait pas comparable à celui du backtest, et la comparaison est
précisément ce qui a de la valeur.

Une issue, une fois établie, n'est **jamais recalculée**. Le moteur ne dispose
que d'une fenêtre glissante de bougies ; sans ce gel, un signal résolu
sortirait de la fenêtre et redeviendrait indéterminé. C'est aussi ce qui rend
le suivi non falsifiable : le verdict est écrit une fois, puis figé dans
l'historique git.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from .backtest import MAX_HOLD, simulate_exit
from .config import SETTINGS
from .paper import _cost_bps
from .store import read, write

log = logging.getLogger("jimbot.ledger")

# Issues définitives : une fois posées, elles ne bougent plus.
FIGEES = {"cible", "stop", "expiration", "hors_portee"}

# Champs produits par `resoudre`. Nommés une fois, parce qu'on a besoin de
# reprendre un relevé précédent à l'identique quand la mesure est impossible.
CHAMPS_RESOLUTION = ("issue", "resolu_le", "prix_sortie", "r_multiple", "bougies",
                     "mfe", "mae", "dernier_prix", "r_courant", "mesure_le")


def _horodatage(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def episodes(emissions: list[dict]) -> list[dict]:
    """Regroupe les émissions en signaux distincts (voir la note d'en-tête)."""
    valides = [e for e in emissions
               if e.get("symbol") and e.get("direction") in ("long", "short")
               and _horodatage(e.get("generated_at", "")) is not None]
    valides.sort(key=lambda e: e["generated_at"])

    fenetre = timedelta(minutes=SETTINGS.alert_cooldown_min)
    ouverts: dict[tuple[str, str], dict] = {}
    out: list[dict] = []

    for e in valides:
        cle = (e["symbol"], e["direction"])
        t = _horodatage(e["generated_at"])
        courant = ouverts.get(cle)
        if courant is not None and t - _horodatage(courant["derniere_emission"]) <= fenetre:
            courant["derniere_emission"] = e["generated_at"]
            courant["emissions"] += 1
            courant["score_max"] = max(courant["score_max"], float(e.get("score", 0.0)))
            continue

        episode = {
            "id": f"{e['symbol']}:{e['direction']}:{e['generated_at']}",
            "symbol": e["symbol"],
            "label": e.get("label", e["symbol"]),
            "klass": e.get("klass", ""),
            "direction": e["direction"],
            "premiere_emission": e["generated_at"],
            "derniere_emission": e["generated_at"],
            "emissions": 1,
            "score": round(float(e.get("score", 0.0)), 2),
            "score_max": float(e.get("score", 0.0)),
            "regime": e.get("regime", ""),
            "entry": float(e.get("entry", e.get("price", 0.0))),
            "stop": float(e.get("stop", 0.0)),
            "target": float(e.get("target", 0.0)),
            "rr": float(e.get("rr", 0.0)),
        }
        ouverts[cle] = episode
        out.append(episode)

    for ep in out:
        ep["score_max"] = round(ep["score_max"], 2)
        # Publié sur Discord ou resté sous le seuil d'alerte : la distinction
        # compte, car seuls les premiers ont été vus par quelqu'un.
        ep["alerte_discord"] = ep["score_max"] >= SETTINGS.alert_threshold
    out.sort(key=lambda ep: ep["premiere_emission"], reverse=True)
    return out


def resoudre(ep: dict, df: pd.DataFrame | None, mesure_le: str) -> dict:
    """Confronte un épisode aux bougies qui ont suivi son émission."""
    verdict = {"issue": "indetermine", "resolu_le": None, "prix_sortie": None,
               "r_multiple": None, "bougies": 0, "mfe": 0.0, "mae": 0.0,
               "dernier_prix": None, "r_courant": None, "mesure_le": mesure_le}

    debut = _horodatage(ep["premiere_emission"])
    if df is None or df.empty or debut is None or ep["stop"] <= 0:
        verdict["issue"] = "indetermine"
        return verdict

    # La fenêtre de bougies est glissante : si elle commence après l'émission,
    # le début du trade est perdu et le reconstituer serait une invention.
    if df.index[0] > debut:
        verdict["issue"] = "hors_portee"
        return verdict

    futur = df[df.index > debut]
    if futur.empty:
        verdict["issue"] = "en_cours"
        return verdict

    bps = _cost_bps(ep["klass"]) / 2.0 / 10_000.0
    adverse = 1.0 if ep["direction"] == "long" else -1.0
    entree = ep["entry"] * (1.0 + adverse * bps)
    risque = abs(entree - ep["stop"])
    if risque <= 0:
        verdict["issue"] = "indetermine"
        return verdict

    issue, brut, bougies, mfe, mae = simulate_exit(
        futur, ep["direction"], entree, ep["stop"], ep["target"], ep["klass"])
    signe = 1.0 if ep["direction"] == "long" else -1.0

    verdict["bougies"] = int(bougies)
    verdict["mfe"] = mfe
    verdict["mae"] = mae
    verdict["dernier_prix"] = round(float(futur["close"].iloc[-1]), 8)
    verdict["r_courant"] = round(
        signe * (float(futur["close"].iloc[-1]) - entree) / risque, 3)

    if issue in {"invalide", "tronque"}:
        # « tronque » = les bougies disponibles s'arrêtent avant que le marché
        # ait tranché. Le trade court toujours.
        verdict["issue"] = "en_cours" if issue == "tronque" else "indetermine"
        return verdict

    sortie = brut * (1.0 - adverse * bps)
    verdict["issue"] = issue
    verdict["prix_sortie"] = round(sortie, 8)
    verdict["r_multiple"] = round(signe * (sortie - entree) / risque, 3)
    verdict["resolu_le"] = futur.index[bougies - 1].isoformat() if bougies else None
    return verdict


def resume(signaux: list[dict]) -> dict:
    """Bilan d'ensemble, en ne comptant que ce qui est tranché."""
    par_issue: dict[str, int] = {}
    for s in signaux:
        issue = s.get("issue", "indetermine")
        par_issue[issue] = par_issue.get(issue, 0) + 1

    tranches = [s for s in signaux
                if s.get("issue") in {"cible", "stop", "expiration"}]
    rs = [s["r_multiple"] for s in tranches if s.get("r_multiple") is not None]
    gagnants = sum(1 for s in tranches if s["issue"] == "cible")

    return {
        "emissions": sum(s.get("emissions", 0) for s in signaux),
        "signaux": len(signaux),
        "publies_discord": sum(1 for s in signaux if s.get("alerte_discord")),
        "par_issue": par_issue,
        "tranches": len(tranches),
        "win_rate": round(gagnants / len(tranches) * 100, 1) if tranches else None,
        "esperance_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(sum(rs), 3) if rs else None,
        # Un taux de réussite sur une poignée de trades ne mesure rien. Le
        # fichier porte lui-même cette réserve, pour qu'elle survive à
        # l'endroit où il est lu.
        "significatif": len(tranches) >= 30,
    }


def enregistrer(emissions: list[dict], candles: dict[str, pd.DataFrame],
                mesure_le: str) -> dict:
    """Met à jour le suivi des signaux émis et l'écrit dans `data/suivi.json`."""
    ancien = read("suivi", {}) or {}
    connus = {s["id"]: s for s in ancien.get("signaux", []) if isinstance(s, dict) and "id" in s}

    signaux = []
    for ep in episodes(emissions):
        precedent = connus.get(ep["id"], {})
        if precedent.get("issue") in FIGEES:
            # Verdict déjà rendu : on ne conserve que le comptage d'émissions,
            # qui peut avoir augmenté depuis.
            fige = {**precedent}
            fige["emissions"] = max(fige.get("emissions", 0), ep["emissions"])
            fige["derniere_emission"] = max(
                fige.get("derniere_emission", ""), ep["derniere_emission"])
            signaux.append(fige)
            continue

        df = candles.get(ep["symbol"])
        if df is None and precedent.get("issue"):
            # Une source de données en panne ne doit pas effacer la dernière
            # mesure connue d'un trade en cours. On garde le relevé précédent
            # tel quel, avec sa date : il dit de quand il date.
            log.warning("%s : aucune bougie ce cycle, mesure précédente conservée",
                        ep["symbol"])
            signaux.append({**ep, **{k: precedent[k] for k in CHAMPS_RESOLUTION
                                     if k in precedent}})
            continue
        signaux.append({**ep, **resoudre(ep, df, mesure_le)})

    payload = {
        "generated_at": mesure_le,
        "horizon_bougies": MAX_HOLD,
        "seuil_alerte": SETTINGS.alert_threshold,
        "fenetre_regroupement_min": SETTINGS.alert_cooldown_min,
        "methode": (
            "Émissions regroupées en signaux quand elles sont espacées de moins que "
            "le délai anti-spam Discord. Issue déterminée sur les bougies horaires "
            "suivant la première émission, avec les règles du backtest : le stop "
            "prime en cas d'ambiguïté sur une même bougie, l'horizon est borné à "
            f"{MAX_HOLD} bougies, et les coûts de transaction sont appliqués aux "
            "deux jambes dans le sens défavorable. Une issue établie n'est jamais "
            "recalculée."
        ),
        "resume": resume(signaux),
        "signaux": signaux,
    }
    write("suivi", payload)
    r = payload["resume"]
    log.info("suivi : %d signal(aux) sur %d émission(s), %d tranché(s), %s",
             r["signaux"], r["emissions"], r["tranches"],
             f"espérance {r['esperance_r']:+.3f} R" if r["esperance_r"] is not None
             else "aucune espérance mesurable")
    return payload
