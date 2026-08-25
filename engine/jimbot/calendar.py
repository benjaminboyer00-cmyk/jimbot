"""Échéances de marché à venir.

Deux sources, et deux seulement — parce que ce sont les deux qu'on peut
garantir :

1. **Les échéances mécaniques.** Certaines dates se déduisent du calendrier
   par une règle, sans aucune donnée externe : les chiffres de l'emploi
   américain tombent le premier vendredi du mois, les expirations d'options
   le troisième vendredi, les fins de trimestre déclenchent des
   rééquilibrages. Ces dates sont exactes par construction.

2. **Ce que la presse annonce.** Les dépêches signalent constamment ce qui
   arrive (« ahead of Thursday's CPI », « the Fed meets next week »). On
   extrait ces mentions avec leur source, ce qui permet au lecteur de
   vérifier.

Ce qui n'est **pas** fait ici : inscrire en dur un calendrier de réunions de
banques centrales. Ces dates changent, et une date fausse présentée comme
certaine est pire que pas de date du tout. Le module signale l'échéance
lorsque la presse la mentionne, et se tait sinon.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone


@dataclass
class Event:
    """Une échéance à venir."""

    date: str          # ISO 8601
    days_ahead: int
    label: str
    kind: str          # "mecanique" | "presse"
    impact: str        # "eleve" | "moyen" | "faible"
    detail: str
    source: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Échéances mécaniques
# --------------------------------------------------------------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-ième `weekday` du mois (0 = lundi)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_business_day(year: int, month: int) -> date:
    """Dernier jour ouvré du mois."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:      # samedi ou dimanche
        d -= timedelta(days=1)
    return d


def mechanical_events(today: date | None = None, horizon_days: int = 21) -> list[Event]:
    """Échéances déductibles du calendrier, sans source externe."""
    today = today or datetime.now(timezone.utc).date()
    limit = today + timedelta(days=horizon_days)
    out: list[Event] = []

    def add(when: date, label: str, impact: str, detail: str) -> None:
        if today <= when <= limit:
            out.append(Event(
                date=when.isoformat(), days_ahead=(when - today).days,
                label=label, kind="mecanique", impact=impact, detail=detail))

    # On balaie le mois courant et le suivant : l'horizon peut les chevaucher.
    for offset in (0, 1):
        y = today.year + (today.month + offset - 1) // 12
        m = (today.month + offset - 1) % 12 + 1

        add(_nth_weekday(y, m, 4, 1),
            "Emploi américain (NFP)", "eleve",
            "Publié le premier vendredi du mois. Déplace le dollar, l'or et "
            "les indices : c'est la donnée qui conditionne les anticipations "
            "de taux à court terme.")

        add(_nth_weekday(y, m, 4, 3),
            "Expiration mensuelle des options", "moyen",
            "Troisième vendredi. La couverture des vendeurs d'options tend à "
            "figer le prix près des principaux strikes, puis à le libérer "
            "brutalement après l'échéance.")

        fin = _last_business_day(y, m)
        trimestre = m in (3, 6, 9, 12)
        add(fin,
            "Fin de trimestre" if trimestre else "Fin de mois", 
            "moyen" if trimestre else "faible",
            "Rééquilibrage des portefeuilles institutionnels. Les flux sont "
            "mécaniques et sans rapport avec la valorisation, ce qui perturbe "
            "temporairement les signaux techniques."
            + (" Les fins de trimestre concentrent les volumes les plus élevés."
               if trimestre else ""))

    # Week-end : le forex et les indices ferment, la crypto non. Un signal
    # ouvert le vendredi soir traverse deux jours sans possibilité de sortie.
    days_to_friday = (4 - today.weekday()) % 7
    friday = today + timedelta(days=days_to_friday)
    if days_to_friday <= 3:
        add(friday, "Fermeture hebdomadaire (forex et indices)", "faible",
            "Le forex et les indices cessent de coter jusqu'à dimanche soir. "
            "Une position conservée traverse le week-end sans stop exécutable, "
            "et peut rouvrir sur un écart de cotation.")

    return sorted(out, key=lambda e: e.date)


# --------------------------------------------------------------------------
# Échéances annoncées par la presse
# --------------------------------------------------------------------------
# Tournures signalant qu'un événement est à venir, et non déjà survenu.
FORWARD_PATTERNS = [
    r"\bahead of\b", r"\bnext week\b", r"\bnext month\b", r"\bthis week\b",
    r"\bdue (?:on|this|next)\b", r"\bscheduled for\b", r"\bwill meet\b",
    r"\bset to (?:meet|decide|release|publish|announce)\b",
    r"\bexpected (?:on|this|next|to be released)\b", r"\bupcoming\b",
    r"\bawait(?:s|ing)?\b", r"\bin focus\b", r"\bpreview\b",
    r"\bla semaine prochaine\b", r"\bprochaine réunion\b", r"\bà venir\b",
    r"\battendu(?:e|s)? (?:cette|la semaine|le)\b", r"\bavant la publication\b",
]

# Sujets dont l'annonce a un impact de marché, avec leur portée.
WATCHED_TOPICS: dict[str, tuple[str, str]] = {
    "fomc": ("eleve", "Décision de la Réserve fédérale"),
    "fed meeting": ("eleve", "Réunion de la Réserve fédérale"),
    "rate decision": ("eleve", "Décision de taux"),
    "interest rate decision": ("eleve", "Décision de taux"),
    "cpi": ("eleve", "Inflation américaine (CPI)"),
    "inflation data": ("eleve", "Publication d'inflation"),
    "pce": ("eleve", "Inflation PCE, indicateur suivi par la Fed"),
    "jobs report": ("eleve", "Rapport sur l'emploi"),
    "payrolls": ("eleve", "Chiffres de l'emploi"),
    "ecb": ("eleve", "Banque centrale européenne"),
    "bank of england": ("moyen", "Banque d'Angleterre"),
    "bank of japan": ("moyen", "Banque du Japon"),
    "gdp": ("moyen", "Produit intérieur brut"),
    "earnings": ("moyen", "Résultats d'entreprises"),
    "opec": ("moyen", "Réunion de l'OPEP"),
    "jackson hole": ("eleve", "Symposium de Jackson Hole"),
    "powell": ("eleve", "Prise de parole de Powell"),
    "lagarde": ("moyen", "Prise de parole de Lagarde"),
    "summit": ("moyen", "Sommet international"),
    "election": ("moyen", "Échéance électorale"),
    "ceasefire talks": ("eleve", "Négociations de cessez-le-feu"),
    "peace talks": ("eleve", "Négociations de paix"),
    "tariff": ("moyen", "Échéance douanière"),
    "deadline": ("moyen", "Échéance annoncée"),
    "halving": ("moyen", "Halving"),
    "unlock": ("moyen", "Déblocage de jetons"),
    # Équivalents français : les flux France 24 et Le Monde emploient les
    # sigles francophones, invisibles pour les clés anglaises ci-dessus.
    "bce": ("eleve", "Banque centrale européenne"),
    "réserve fédérale": ("eleve", "Réserve fédérale"),
    "banque centrale": ("moyen", "Banque centrale"),
    "taux directeur": ("eleve", "Décision de taux"),
    "inflation": ("moyen", "Publication d'inflation"),
    "chômage": ("moyen", "Chiffres du chômage"),
    "opep": ("moyen", "Réunion de l'OPEP"),
    "sommet": ("moyen", "Sommet international"),
    "élection": ("moyen", "Échéance électorale"),
    "négociations": ("moyen", "Négociations en cours"),
}


def press_events(articles: list[dict], limit: int = 8) -> list[Event]:
    """Extrait les échéances que la presse annonce comme à venir.

    Un article n'est retenu que s'il combine une tournure prospective et un
    sujet suivi : « ahead of Thursday's CPI » compte, « CPI rose 0.2% »
    non — le second raconte le passé.
    """
    now = datetime.now(timezone.utc)
    out: list[Event] = []
    seen: set[str] = set()

    for a in articles:
        text = f"{a.get('title', '')}".lower()
        if not any(re.search(p, text) for p in FORWARD_PATTERNS):
            continue

        for topic, (impact, label) in WATCHED_TOPICS.items():
            if topic not in text or label in seen:
                continue
            seen.add(label)
            out.append(Event(
                # Aucune date n'est inventée : seul l'article la connaît, et
                # elle reste dans son titre.
                date=now.date().isoformat(),
                days_ahead=0,
                label=label,
                kind="presse",
                impact=impact,
                detail=a.get("title", "")[:180],
                source=a.get("source", ""),
                url=a.get("url", ""),
            ))
            break

    order = {"eleve": 0, "moyen": 1, "faible": 2}
    out.sort(key=lambda e: order.get(e.impact, 3))
    return out[:limit]


def upcoming(articles: list[dict], today: date | None = None) -> dict:
    """Agenda complet : échéances mécaniques et annonces de presse."""
    mech = mechanical_events(today)
    press = press_events(articles)
    return {
        "mechanical": [e.to_dict() for e in mech],
        "press": [e.to_dict() for e in press],
        "high_impact": sum(1 for e in mech + press if e.impact == "eleve"),
        "next": mech[0].to_dict() if mech else None,
    }
