"""Mémoire du moteur : la trajectoire de chaque actif, scan après scan.

Jusqu'ici le dépôt ne gardait que deux choses : l'état du dernier scan
(`latest.json`, écrasé à chaque passage) et la liste des signaux émis
(`signals.json`). Entre les deux, tout ce que le moteur avait vu disparaissait
— le prix d'un actif il y a trois jours, le score qu'il portait alors, le
régime dans lequel il évoluait. On ne pouvait donc rien dire de ce qui a suivi
un signal, ni tracer autre chose que le présent.

Ce module tient cette mémoire. Elle est le socle des deux choses qui en
dépendent : la page d'un actif, et la redevabilité (`ledger.py`), qui confronte
chaque signal émis à ce que le marché a fait ensuite.

Format
------
Colonnaire et volontairement avare. Un point pèse une ligne :

    ["2026-09-05T07:20:00+00:00", 4358.1, 58.1, 2, 1]
      horodatage                  prix     score  régime  signal émis

- le **score est signé** : positif à l'achat, négatif à la vente. C'est la
  même convention que partout ailleurs sur le site ;
- le **régime** est un indice dans la légende `regimes`, parce que le répéter
  en toutes lettres sur des milliers de lignes coûterait plus cher que tout le
  reste du fichier réuni ;
- **signal** vaut 1 quand le moteur a réellement émis un signal à ce passage,
  0 sinon. Sans ce drapeau, il faudrait recroiser `signals.json` pour savoir
  quels points du graphique ont donné lieu à une alerte.

Les points sont rangés du plus ancien au plus récent — l'inverse des autres
historiques du dépôt, qui listent les plus récents en tête. Une série
temporelle se lit dans le sens du temps, et l'ajout se fait en queue.

La sérialisation met un point par ligne. `json.dump` aurait éclaté chaque
ligne en cinq, multipliant la taille du fichier par quatre ; tout écrire sur
une seule ligne aurait rendu chaque scan illisible dans un diff git. Un point
par ligne donne les deux : un fichier compact et un diff qui montre exactement
les seize lignes ajoutées par un scan.
"""
from __future__ import annotations

import json
import logging

from .store import read, write_raw

log = logging.getLogger("jimbot.history")

# Nombre de points conservés par actif.
#
# La couverture réelle de l'ordonnanceur GitHub tourne autour de vingt scans
# par jour : 400 points couvrent donc environ trois semaines. Au-delà, le
# fichier grossirait sans que le graphique d'un actif y gagne quoi que ce soit
# — et il est réécrit puis committé à chaque scan. La redevabilité, elle, ne
# dépend pas de cette fenêtre : `ledger.py` fige l'issue d'un signal une fois
# pour toutes, elle ne se relit jamais dans l'historique.
MAX_POINTS = 400

# Légende des régimes. L'ordre est **figé** : les points stockent un indice,
# le réordonner réécrirait le passé. Un régime inconnu est ajouté en queue.
REGIMES = ["tendance_haussière", "tendance_baissière", "range", "chaotique"]

CHAMPS = ["t", "prix", "score", "regime", "signal"]


def _arrondi_prix(v: float) -> float:
    """Arrondit un prix à une précision utile à son ordre de grandeur.

    Écrire le Bitcoin avec huit décimales ou un memecoin avec deux serait
    absurde dans les deux sens. Les seuils reprennent ceux de l'affichage, de
    sorte que le graphique ne montre jamais plus de précision que le fichier
    n'en contient.
    """
    if v >= 1000:
        return round(v, 2)
    if v >= 1:
        return round(v, 4)
    if v >= 0.01:
        return round(v, 6)
    return round(v, 10)


def score_signe(signal: dict) -> float:
    """Score porteur de son sens.

    `score` est une magnitude non signée et `direction` n'est renseignée
    qu'au-delà du seuil : pour un actif resté neutre, c'est le signe de
    `raw_score` qui porte l'orientation. Même convention que `signed()` côté
    site, et il faut qu'elle le reste.
    """
    score = float(signal.get("score", 0.0))
    direction = signal.get("direction", "neutre")
    if direction == "short":
        return -score
    if direction == "long":
        return score
    return score if float(signal.get("raw_score", 0.0)) >= 0 else -score


def point(signal: dict, legende: list[str], horodatage: str) -> list:
    """Convertit un signal sérialisé en un point d'historique."""
    regime = signal.get("regime")
    nom = regime.get("name") if isinstance(regime, dict) else (regime or "")
    if nom not in legende:
        legende.append(nom)
    return [
        horodatage,
        _arrondi_prix(float(signal.get("price", 0.0))),
        round(score_signe(signal), 1),
        legende.index(nom),
        1 if signal.get("direction", "neutre") != "neutre" else 0,
    ]


def _vide() -> dict:
    return {"generated_at": None, "champs": CHAMPS, "regimes": list(REGIMES),
            "points_max": MAX_POINTS, "actifs": {}}


def charger() -> dict:
    """Lit l'historique existant, en réparant une structure inattendue."""
    h = read("history", None)
    if not isinstance(h, dict) or not isinstance(h.get("actifs"), dict):
        if h is not None:
            log.warning("historique illisible ou corrompu, réinitialisé")
        return _vide()
    h.setdefault("regimes", list(REGIMES))
    h.setdefault("champs", CHAMPS)
    h["points_max"] = MAX_POINTS
    return h


def fusionner(hist: dict, signaux: list[dict], horodatage: str) -> dict:
    """Insère les points d'un scan dans l'historique, en place.

    Un scan rejoué ou un rétro-remplissage qui chevauche l'existant ne doit
    pas créer de doublon : un point dont l'horodatage est déjà présent
    **remplace** l'ancien plutôt que de s'ajouter à lui.
    """
    legende = hist["regimes"]
    for sig in signaux:
        symbole = sig.get("symbol")
        if not symbole:
            continue
        actif = hist["actifs"].setdefault(
            symbole,
            {"label": sig.get("label", symbole), "klass": sig.get("klass", ""),
             "points": []},
        )
        # Le libellé et la classe peuvent avoir changé depuis le premier point.
        actif["label"] = sig.get("label", actif.get("label", symbole))
        actif["klass"] = sig.get("klass", actif.get("klass", ""))

        p = point(sig, legende, horodatage)
        points = actif["points"]
        if points and points[-1][0] == horodatage:
            points[-1] = p          # cas courant : on refait le dernier scan
        elif any(q[0] == horodatage for q in points):
            for i, q in enumerate(points):
                if q[0] == horodatage:
                    points[i] = p
                    break
        else:
            points.append(p)
    return hist


def ordonner(hist: dict) -> dict:
    """Remet chaque série dans l'ordre du temps et applique la rétention."""
    for actif in hist["actifs"].values():
        actif["points"].sort(key=lambda p: p[0])
        if len(actif["points"]) > MAX_POINTS:
            actif["points"] = actif["points"][-MAX_POINTS:]
    return hist


def serialiser(hist: dict) -> str:
    """Sérialise avec un point par ligne (voir la note d'en-tête)."""
    def s(v) -> str:
        return json.dumps(v, ensure_ascii=False)

    lignes = [
        "{",
        f'  "generated_at": {s(hist.get("generated_at"))},',
        f'  "champs": {s(hist["champs"])},',
        f'  "regimes": {s(hist["regimes"])},',
        f'  "points_max": {hist["points_max"]},',
        '  "actifs": {',
    ]
    symboles = sorted(hist["actifs"])
    for i, sym in enumerate(symboles):
        actif = hist["actifs"][sym]
        lignes += [
            f"    {s(sym)}: {{",
            f'      "label": {s(actif.get("label", sym))},',
            f'      "klass": {s(actif.get("klass", ""))},',
            '      "points": [',
        ]
        points = actif["points"]
        for j, p in enumerate(points):
            virgule = "" if j == len(points) - 1 else ","
            lignes.append(f"        {s(p)}{virgule}")
        lignes += ["      ]", "    }" + ("" if i == len(symboles) - 1 else ",")]
    lignes += ["  }", "}", ""]
    return "\n".join(lignes)


def enregistrer(signaux: list[dict], horodatage: str) -> dict:
    """Ajoute le scan courant à l'historique et l'écrit."""
    hist = ordonner(fusionner(charger(), signaux, horodatage))
    hist["generated_at"] = horodatage
    write_raw("history", serialiser(hist))
    total = sum(len(a["points"]) for a in hist["actifs"].values())
    log.info("historique : %d actif(s), %d point(s) conservé(s)",
             len(hist["actifs"]), total)
    return hist
