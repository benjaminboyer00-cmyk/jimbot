"""Exécution des signaux sur un compte MetaTrader, depuis le cloud.

Le problème que ce module résout est précis. L'application MetaTrader mobile
n'exécute aucun code, et GitHub Actions ne peut pas faire tourner un terminal
MetaTrader. Entre le moteur, qui vit dans un runner CI, et le téléphone, qui
n'affiche qu'un compte, il manquait la pièce qui passe réellement les ordres.

MetaApi est cette pièce : un service qui tient une connexion permanente à un
compte MetaTrader et l'expose en REST. Le moteur y poste ses ordres, ils
arrivent dans le compte, et l'application mobile les affiche — sans qu'aucune
machine de l'utilisateur ne soit allumée.

**Ce module refuse par défaut de travailler sur un compte réel.** La
vérification porte sur le champ `type` renvoyé par le courtier, pas sur une
option locale. Passer outre demande une variable d'environnement explicite.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .config import RISK, _env
from .datasources.base import DataError, http_get_json, http_post_json
from . import mt_symbols

log = logging.getLogger("jimbot.broker")

# Chaque région MetaApi a son domaine ; poster sur le mauvais renvoie 404.
REGION_DEFAUT = "new-york"

DEMO = "ACCOUNT_TRADE_MODE_DEMO"

# Marge de sécurité sur le nombre de positions ouvertes simultanément par ce
# module. Le portefeuille papier a ses propres plafonds ; ceux-ci protègent le
# compte réel de courtier, qui n'est pas simulé.
MAX_POSITIONS = int(_env("JIMBOT_BROKER_MAX_POSITIONS", "5"))


@dataclass
class Compte:
    """Ce que le courtier dit du compte, tel qu'il le dit."""

    login: str
    serveur: str
    courtier: str
    devise: str
    solde: float
    equite: float
    type_compte: str
    trading_autorise: bool

    @property
    def est_demo(self) -> bool:
        return self.type_compte == DEMO


class BrokerError(RuntimeError):
    """Refus ou échec côté courtier. Ne doit jamais interrompre un scan."""


class MetaApi:
    """Client REST minimal : compte, positions, spécifications, ordres.

    Volontairement réduit à ce que le moteur utilise. Un SDK complet
    apporterait une dépendance lourde et des websockets dont un runner CI, qui
    vit quelques minutes, n'a aucun usage.
    """

    def __init__(self, token: str, account_id: str, region: str = REGION_DEFAUT):
        if not token or not account_id:
            raise BrokerError("METAAPI_TOKEN ou METAAPI_ACCOUNT_ID manquant")
        self.token = token
        self.account_id = account_id
        self.base = f"https://mt-client-api-v1.{region}.agiliumtrade.ai"

    # -- lecture ------------------------------------------------------------
    def _get(self, chemin: str) -> dict | list:
        url = f"{self.base}/users/current/accounts/{self.account_id}{chemin}"
        try:
            return http_get_json(url, headers={"auth-token": self.token})
        except DataError as e:
            raise BrokerError(f"lecture {chemin} : {e}") from e

    def compte(self) -> Compte:
        d = self._get("/account-information")
        if not isinstance(d, dict):
            raise BrokerError("réponse inattendue pour /account-information")
        return Compte(
            login=str(d.get("login", "")),
            serveur=str(d.get("server", "")),
            courtier=str(d.get("broker", "")),
            devise=str(d.get("currency", "")),
            solde=float(d.get("balance", 0.0)),
            equite=float(d.get("equity", 0.0)),
            type_compte=str(d.get("type", "")),
            trading_autorise=bool(d.get("tradeAllowed", False)),
        )

    def positions(self) -> list[dict]:
        d = self._get("/positions")
        return d if isinstance(d, list) else []

    def prix(self, symbole: str) -> float | None:
        """Prix courant d'un instrument, ou None s'il est indisponible."""
        try:
            d = self._get(f"/symbols/{symbole}/current-price")
        except BrokerError:
            return None
        if not isinstance(d, dict):
            return None
        for cle in ("bid", "ask", "last"):
            v = d.get(cle)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    def specification(self, symbole: str) -> dict | None:
        try:
            d = self._get(f"/symbols/{symbole}/specification")
        except BrokerError:
            return None
        return d if isinstance(d, dict) else None

    # -- résolution du nom d'instrument -------------------------------------
    def resoudre(self, symbole_interne: str) -> str | None:
        """Premier alias que ce courtier reconnaît, ou None.

        Un instrument absent n'est pas une erreur : les courtiers ne proposent
        pas les mêmes marchés, et les autres signaux restent traitables.
        """
        for alias in mt_symbols.aliases(symbole_interne):
            if self.specification(alias) is not None:
                return alias
        return None

    # -- écriture -----------------------------------------------------------
    def ordre(self, payload: dict) -> dict:
        url = f"{self.base}/users/current/accounts/{self.account_id}/trade"
        try:
            return http_post_json(url, payload, headers={"auth-token": self.token})
        except DataError as e:
            raise BrokerError(f"ordre refusé : {e}") from e


def taux_vers_compte(api: "MetaApi", devise_profit: str, devise_compte: str) -> float | None:
    """Combien vaut une unité de `devise_profit` en `devise_compte`.

    Sans ce taux, le dimensionnement est faux dès que l'instrument n'est pas
    coté dans la devise du compte. Sur USDJPY, la distance au stop s'exprime en
    yens : 141 unités à 0,567 ¥ font 80 ¥, soit environ 0,51 $ — pas les 80 $
    que le moteur croyait risquer. La position sortait cent cinquante-six fois
    trop petite. Dans l'autre sens — une devise de cotation plus forte que celle
    du compte — l'erreur irait vers le trop gros, et coûterait.

    Renvoie None quand aucune paire de conversion n'est disponible. L'appelant
    doit alors refuser l'ordre : envoyer un volume dont on sait qu'il est faux
    est pire que ne rien envoyer.
    """
    if not devise_profit or not devise_compte:
        return None
    if devise_profit == devise_compte:
        return 1.0

    # PROFITCOMPTE : le prix donne directement le taux.
    direct = api.prix(f"{devise_profit}{devise_compte}")
    if direct:
        return direct
    # COMPTEPROFIT : le taux est l'inverse.
    inverse = api.prix(f"{devise_compte}{devise_profit}")
    if inverse:
        return 1.0 / inverse
    return None


def volume_pour(spec: dict, unites: float) -> float:
    """Convertit une quantité d'instrument en volume MetaTrader, en lots.

    Le moteur dimensionne en unités de l'actif — c'est ce qui rend une perte au
    stop égale au risque décidé. MetaTrader compte en lots, et un lot ne vaut
    pas la même chose d'un instrument à l'autre : `contractSize` fait la
    conversion, `volumeStep` impose l'arrondi.

    L'arrondi se fait vers le bas. Arrondir vers le haut ferait dépasser le
    risque décidé, ce qui est précisément ce que tout le dimensionnement
    cherche à empêcher.
    """
    taille = float(spec.get("contractSize") or 1.0)
    pas = float(spec.get("volumeStep") or 0.01)
    mini = float(spec.get("minVolume") or 0.01)
    maxi = float(spec.get("maxVolume") or 1e9)
    if taille <= 0 or pas <= 0:
        return 0.0

    brut = unites / taille
    # La division entière sur des flottants perd un pas : 12,34 // 0,01 rend
    # 1233 et non 1234, parce que 12,34 n'est pas représentable exactement en
    # binaire. La tolérance rattrape ce seul cas sans jamais arrondir vers le
    # haut d'un pas entier.
    pas_entiers = int(brut / pas + 1e-9)
    arrondi = pas_entiers * pas
    if arrondi < mini:
        # Sous le lot minimal, on n'ouvre rien plutôt que d'arrondir vers le
        # haut : le trade coûterait plus que le risque accepté.
        return 0.0
    return round(min(arrondi, maxi), 8)


def client_id(signal: dict) -> str:
    """Identifiant stable d'un signal, pour ne pas l'ouvrir deux fois.

    Le scan réémet la même configuration à chaque passage tant qu'elle tient.
    Sans cet identifiant, chaque quart d'heure rouvrirait la même position, et
    une configuration qui tient une journée produirait quatre-vingt-seize
    positions identiques.
    """
    horodatage = str(signal.get("generated_at", ""))[:13]  # à l'heure près
    brut = f"jimbot_{signal.get('symbol', '')}_{horodatage}"
    return "".join(c if c.isalnum() or c == "_" else "_" for c in brut)[:36]


# --------------------------------------------------------------------------
# Synchronisation : porter les signaux du moteur sur le compte
# --------------------------------------------------------------------------
def _client() -> MetaApi:
    return MetaApi(
        token=_env("METAAPI_TOKEN", ""),
        account_id=_env("METAAPI_ACCOUNT_ID", ""),
        region=_env("METAAPI_REGION", REGION_DEFAUT),
    )


def actif() -> bool:
    """L'exécution sur compte courtier est-elle demandée ?

    Éteinte par défaut. Un dépôt cloné, un fork, une exécution locale : rien de
    tout cela ne doit se mettre à passer des ordres parce qu'un jeton traîne
    dans l'environnement.
    """
    return _env("JIMBOT_BROKER", "0") not in ("0", "", "false", "no")


def synchroniser(signaux: list[dict], *, dry_run: bool = False) -> dict:
    """Porte les configurations retenues sur le compte MetaTrader.

    Ne lève jamais : un courtier indisponible doit laisser le scan se terminer
    et committer ses données. Tout ce qui échoue est journalisé et rendu dans
    le compte-rendu.
    """
    rapport: dict = {"actif": True, "ordres": [], "ignores": [], "erreur": None}
    try:
        api = _client()
        compte = api.compte()
    except BrokerError as e:
        log.warning("courtier indisponible : %s", e)
        return {**rapport, "actif": False, "erreur": str(e)}

    rapport["compte"] = {
        "login": compte.login, "courtier": compte.courtier,
        "serveur": compte.serveur, "devise": compte.devise,
        "solde": compte.solde, "equite": compte.equite,
        "type": compte.type_compte, "demo": compte.est_demo,
    }

    # Garde-fou principal. Le type vient du serveur du courtier, pas d'un
    # réglage local : c'est la seule affirmation qu'on ne peut pas se faire à
    # soi-même par erreur.
    autoriser_reel = _env("JIMBOT_BROKER_ALLOW_LIVE", "0") not in ("0", "", "false", "no")
    if not compte.est_demo and not autoriser_reel:
        msg = (f"compte {compte.login} de type {compte.type_compte} : ce n'est pas "
               f"un compte de démonstration, aucun ordre n'est transmis")
        log.error("REFUS — %s", msg)
        return {**rapport, "erreur": msg}

    if not compte.trading_autorise:
        msg = f"le courtier refuse le trading sur le compte {compte.login}"
        log.warning(msg)
        return {**rapport, "erreur": msg}

    ouvertes = api.positions()
    deja = {p.get("symbol") for p in ouvertes}
    deja_ids = {p.get("clientId") for p in ouvertes if p.get("clientId")}
    place = 0

    for s in signaux:
        nom = s.get("symbol", "?")

        if not s.get("actionable"):
            continue
        if float(s.get("expected_r", 0.0)) <= 0:
            rapport["ignores"].append({"symbol": nom, "raison": "espérance négative"})
            continue
        if len(ouvertes) + place >= MAX_POSITIONS:
            rapport["ignores"].append({"symbol": nom, "raison": "plafond de positions atteint"})
            continue

        cid = client_id(s)
        if cid in deja_ids:
            rapport["ignores"].append({"symbol": nom, "raison": "déjà ouvert par ce signal"})
            continue

        courtier_nom = api.resoudre(nom)
        if courtier_nom is None:
            rapport["ignores"].append({"symbol": nom, "raison": "instrument absent chez ce courtier"})
            continue
        if courtier_nom in deja:
            rapport["ignores"].append({"symbol": nom, "raison": "position déjà ouverte sur cet instrument"})
            continue

        spec = api.specification(courtier_nom)
        if spec is None:
            rapport["ignores"].append({"symbol": nom, "raison": "spécification indisponible"})
            continue

        # Le dimensionnement est celui du moteur, appliqué au solde réel du
        # compte : c'est la même règle que le portefeuille papier et que le
        # calculateur du site, pas une troisième.
        from . import risk as R
        taille = R.position_size(compte.solde, float(s["entry"]), float(s["stop"]),
                                 s.get("klass", "crypto"), score=float(s.get("score", 60.0)))
        unites = float(taille.get("units", 0.0))

        # Le moteur raisonne comme si l'instrument était coté dans la devise du
        # compte. Quand ce n'est pas le cas, la quantité doit être divisée par
        # le taux, faute de quoi la perte au stop n'est pas celle qu'on a
        # décidée. Sans taux disponible, on refuse.
        devise_profit = str(spec.get("profitCurrency") or compte.devise)
        taux = taux_vers_compte(api, devise_profit, compte.devise)
        if taux is None or taux <= 0:
            rapport["ignores"].append({
                "symbol": nom,
                "raison": f"conversion {devise_profit} vers {compte.devise} indisponible : "
                          f"le volume serait faux"})
            continue
        unites = unites / taux

        volume = volume_pour(spec, unites)
        if volume <= 0:
            rapport["ignores"].append({
                "symbol": nom,
                "raison": f"volume sous le lot minimal du courtier ({spec.get('minVolume')})"})
            continue

        payload = {
            "actionType": "ORDER_TYPE_BUY" if s["direction"] == "long" else "ORDER_TYPE_SELL",
            "symbol": courtier_nom,
            "volume": volume,
            "stopLoss": round(float(s["stop"]), int(spec.get("digits") or 5)),
            "takeProfit": round(float(s["target"]), int(spec.get("digits") or 5)),
            "clientId": cid,
            "comment": "jimbot",
        }

        if dry_run:
            rapport["ordres"].append({**payload, "simule": True})
            place += 1
            continue

        try:
            reponse = api.ordre(payload)
        except BrokerError as e:
            rapport["ignores"].append({"symbol": nom, "raison": str(e)})
            continue

        rapport["ordres"].append({**payload, "reponse": reponse})
        deja.add(courtier_nom)
        deja_ids.add(cid)
        place += 1
        log.info("ordre transmis : %s %s %.2f lot(s)", payload["actionType"],
                 courtier_nom, volume)

    return rapport
