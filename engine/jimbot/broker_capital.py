"""Exécution des signaux sur un compte de démonstration Capital.com.

Existe parce que MetaApi facture la connexion elle-même, indépendamment du
compte MetaTrader : un compte de démonstration ne coûte rien chez le courtier,
mais le pont qui l'expose en REST est payant, et sans solde il refuse de
déployer — d'où un compte qui reste indéfiniment DISCONNECTED.

Capital.com expose directement son propre moteur en REST, avec un
environnement de démonstration gratuit, et cote presque tout l'univers du
moteur : forex, or, indices et cryptos en CFD. Le téléphone voit les positions
dans leur application, ce qui était l'objectif depuis le début.

Deux différences de vocabulaire avec MetaTrader, portées ici et nulle part
ailleurs :

- Les instruments s'appellent des « epics » et ne suivent aucune nomenclature
  devinable. On les résout par recherche plutôt que par une table d'alias, ce
  qui évite d'en maintenir une seconde.
- Les tailles s'expriment en unités de l'instrument, pas en lots : la taille
  du contrat vaut donc toujours 1, et le dimensionnement du moteur s'applique
  directement.
"""
from __future__ import annotations

import logging
import time

import requests

from .broker import BrokerError, Compte, diagnostic
from .config import _env
from .datasources.base import DataError, http_post_json

log = logging.getLogger("jimbot.broker.capital")

BASE_DEMO = "https://demo-api-capital.backend-capital.com"
BASE_REEL = "https://api-capital.backend-capital.com"

# Epic exact de chaque instrument, vérifié un par un contre l'API.
#
# La première version résolvait par recherche en retenant le premier résultat
# « négociable ». C'était faux d'une façon dangereuse : un samedi soir, le
# forex, les indices et les matières premières sont FERMÉS tandis que la crypto
# se traite en continu. La recherche « GBP/USD » renvoyait donc GBPUSD (fermé)
# puis, plus bas, XRPUSD (ouvert) — et le filtre retenait XRP. Un ordre sur la
# livre serait parti sur Ripple.
#
# La leçon tient en une phrase : on résout un instrument par son **identité**,
# jamais par sa disponibilité. Qu'un marché soit fermé se constate au moment de
# passer l'ordre, cela ne change pas de quel instrument il s'agit.
#
# Les epics sont donc écrits ici et vérifiés par lecture directe. La recherche
# ne sert plus que de repli, et exige une correspondance exacte de nom.
EPICS: dict[str, str] = {
    "XAUUSD": "GOLD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "SPX": "US500",
    "NDX": "US100",
    "DXY": "DXY",
    "VIX": "VIX",
    "BTC-USD": "BTCUSD",
    "ETH-USD": "ETHUSD",
    "SOL-USD": "SOLUSD",
    "BNB-USD": "BNBUSD",
    "XRP-USD": "XRPUSD",
    "DOGE-USD": "DOGEUSD",
    "AVAX-USD": "AVAXUSD",
    "LINK-USD": "LINKUSD",
}

# Nom attendu pour chaque epic, tel que Capital.com le renvoie. Sert à vérifier
# qu'un epic pointe bien sur ce qu'on croit : une table écrite à la main se
# périme, et se tromper d'instrument est la faute la plus coûteuse possible.
NOMS_ATTENDUS: dict[str, str] = {
    "GOLD": "Gold",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "US500": "US 500",
    "US100": "US Tech 100",
    "DXY": "US Dollar Index",
    "VIX": "Volatility Index",
    "BTCUSD": "Bitcoin/USD",
    "ETHUSD": "Ethereum/USD",
    "SOLUSD": "Solana/USD",
    "BNBUSD": "Binance Coin/USD",
    "XRPUSD": "Ripple/USD",
    "DOGEUSD": "DogeCoin/USD",
    "AVAXUSD": "Avalanche/USD",
    "LINKUSD": "ChainLink/USD",
}

# Capital.com limite le débit ; enchaîner seize lectures d'affilée en fait
# échouer une partie, et un instrument « introuvable » pour cause de débit se
# lit comme un instrument absent.
DELAI_ENTRE_APPELS = 0.6


class CapitalCom:
    """Client REST de Capital.com, réduit à ce que le moteur utilise."""

    def __init__(self, cle: str, identifiant: str, mot_de_passe: str,
                 *, demo: bool = True):
        if not (cle and identifiant and mot_de_passe):
            raise BrokerError("CAPITAL_API_KEY, CAPITAL_IDENTIFIER ou "
                              "CAPITAL_PASSWORD manquant")
        self.base = BASE_DEMO if demo else BASE_REEL
        self.demo = demo
        self._cle = cle
        self._identifiant = identifiant
        self._mot_de_passe = mot_de_passe
        self._entetes: dict[str, str] = {}
        self._cache_epic: dict[str, str | None] = {}

    # -- session ------------------------------------------------------------
    def ouvrir_session(self) -> None:
        """Échange les identifiants contre deux jetons de session.

        La session expire après dix minutes d'inactivité. Un scan dure moins
        d'une minute : on en ouvre une par exécution, et l'on ne cherche pas à
        la faire durer.
        """
        try:
            r = requests.post(
                f"{self.base}/api/v1/session",
                json={"identifier": self._identifiant, "password": self._mot_de_passe},
                headers={"X-CAP-API-KEY": self._cle,
                         "Content-Type": "application/json"},
                timeout=20)
        except Exception as e:  # noqa: BLE001
            raise BrokerError(f"session Capital.com : {e}") from e

        if not r.ok:
            detail = (r.text or "")[:200]
            raise BrokerError(
                f"session refusée (HTTP {r.status_code}) sur {self.base}. "
                f"{detail}")

        cst = r.headers.get("CST")
        jeton = r.headers.get("X-SECURITY-TOKEN")
        if not cst or not jeton:
            raise BrokerError("session ouverte mais sans jeton : réponse inattendue")
        self._entetes = {"CST": cst, "X-SECURITY-TOKEN": jeton}

    def _assure_session(self) -> None:
        if not self._entetes:
            self.ouvrir_session()

    def _get(self, chemin: str) -> dict:
        self._assure_session()
        try:
            r = requests.get(f"{self.base}{chemin}", headers=self._entetes, timeout=20)
        except Exception as e:  # noqa: BLE001
            raise BrokerError(f"lecture {chemin} : {e}") from e
        if not r.ok:
            raise BrokerError(f"lecture {chemin} : {diagnostic(f'HTTP {r.status_code}')}")
        return r.json()

    # -- lecture ------------------------------------------------------------
    def compte(self) -> Compte:
        d = self._get("/api/v1/accounts")
        comptes = d.get("accounts") or []
        if not comptes:
            raise BrokerError("aucun compte sur cette clé d'API")
        # Le compte courant, sinon le premier.
        c = next((a for a in comptes if a.get("preferred")), comptes[0])
        solde = (c.get("balance") or {}).get("balance", 0.0)
        return Compte(
            login=str(c.get("accountId", "")),
            serveur="Capital.com " + ("démo" if self.demo else "réel"),
            courtier="Capital.com",
            devise=str(c.get("currency", "")),
            solde=float(solde or 0.0),
            equite=float((c.get("balance") or {}).get("available", solde) or 0.0),
            # Capital.com sépare les environnements par l'URL : c'est l'adresse
            # appelée qui dit s'il s'agit d'une démonstration, et non un champ
            # que l'on pourrait mal lire.
            type_compte="ACCOUNT_TRADE_MODE_DEMO" if self.demo else "ACCOUNT_TRADE_MODE_REAL",
            trading_autorise=True,
        )

    def positions(self) -> list[dict]:
        d = self._get("/api/v1/positions")
        sorties = []
        for p in d.get("positions") or []:
            marche = p.get("market") or {}
            pos = p.get("position") or {}
            sorties.append({
                "symbol": marche.get("epic"),
                "clientId": pos.get("dealReference"),
                "volume": pos.get("size"),
                "type": pos.get("direction"),
                "profit": pos.get("upl"),
            })
        return sorties

    def specification(self, epic: str) -> dict | None:
        """Règles de négociation d'un marché, au format attendu par le moteur."""
        try:
            d = self._get(f"/api/v1/markets/{epic}")
        except BrokerError:
            return None
        regles = d.get("dealingRules") or {}
        instrument = d.get("instrument") or {}
        mini = ((regles.get("minDealSize") or {}).get("value")) or 0.01
        return {
            # Capital.com traite en unités de l'instrument : un « lot » vaut
            # une unité, donc aucune conversion de taille de contrat.
            "contractSize": 1.0,
            "minVolume": float(mini),
            "volumeStep": float(mini),
            "maxVolume": float(((regles.get("maxDealSize") or {}).get("value")) or 1e9),
            "digits": 5,
            "profitCurrency": (instrument.get("currency")
                               or (instrument.get("currencies") or [{}])[0].get("code", "")),
        }

    def prix(self, symbole: str) -> float | None:
        """Prix d'un instrument, désigné par son epic ou par une devise.

        Sert à la conversion de devise, où l'on cherche une paire comme
        « USDJPY » : celle-ci est un epic valide chez Capital.com, on l'utilise
        donc telle quelle.
        """
        epic = EPICS.get(symbole, symbole)
        if not epic:
            return None
        try:
            d = self._get(f"/api/v1/markets/{epic}")
        except BrokerError:
            return None
        snap = d.get("snapshot") or {}
        for cle in ("bid", "offer"):
            v = snap.get(cle)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    # -- résolution d'instrument -------------------------------------------
    def _nom_du_marche(self, epic: str) -> str | None:
        """Nom que le courtier donne à cet epic, ou None s'il n'existe pas."""
        try:
            d = self._get(f"/api/v1/markets/{epic}")
        except BrokerError:
            return None
        return str((d.get("instrument") or {}).get("name") or "") or None

    def resoudre(self, symbole_interne: str) -> str | None:
        """Epic correspondant à un instrument du moteur, ou None.

        Vérifie que l'epic existe *et* que le courtier lui donne bien le nom
        attendu. Sans cette seconde condition, une table qui se périme ferait
        passer un ordre sur un autre instrument sans que rien ne le signale.
        """
        if symbole_interne in self._cache_epic:
            return self._cache_epic[symbole_interne]

        epic = EPICS.get(symbole_interne)
        resultat = None
        if epic:
            nom = self._nom_du_marche(epic)
            attendu = NOMS_ATTENDUS.get(epic)
            if nom is None:
                log.warning("%s : epic %s introuvable chez ce courtier",
                            symbole_interne, epic)
            elif attendu and nom.strip().lower() != attendu.strip().lower():
                # Refus, et non repli sur autre chose : un epic qui ne porte
                # plus le nom attendu peut désigner n'importe quoi.
                log.error("%s : l'epic %s s'appelle « %s » et non « %s ». "
                          "Instrument écarté.", symbole_interne, epic, nom, attendu)
            else:
                resultat = epic

        self._cache_epic[symbole_interne] = resultat
        time.sleep(DELAI_ENTRE_APPELS)
        return resultat

    # -- écriture -----------------------------------------------------------
    def passer_ordre(self, epic: str, sens: str, volume: float,
                     stop: float, objectif: float, cid: str,
                     digits: int = 5) -> dict:
        self._assure_session()
        payload = {
            "epic": epic,
            "direction": "BUY" if sens == "long" else "SELL",
            "size": round(volume, 8),
            "stopLevel": round(stop, digits),
            "profitLevel": round(objectif, digits),
        }
        try:
            reponse = http_post_json(f"{self.base}/api/v1/positions", payload,
                                     headers=self._entetes)
        except DataError as e:
            raise BrokerError(f"ordre refusé : {e}") from e
        return {"envoye": payload, "reponse": reponse}


def depuis_env() -> CapitalCom:
    return CapitalCom(
        cle=_env("CAPITAL_API_KEY", ""),
        identifiant=_env("CAPITAL_IDENTIFIER", ""),
        mot_de_passe=_env("CAPITAL_PASSWORD", ""),
        demo=_env("CAPITAL_DEMO", "1") not in ("0", "", "false", "no"),
    )


def diagnostiquer(cle: str, identifiant: str, mot_de_passe: str) -> dict:
    """Essaie d'ouvrir une session sur les deux environnements.

    Un 401 ne dit pas *lequel* des trois champs est faux. Mais comparer les
    deux environnements, eux, tranche : la clé est la même pour les deux, donc
    si l'un accepte et l'autre refuse, le problème est l'environnement et non
    les identifiants ; si les deux refusent, ce sont les identifiants.

    N'ouvre que des sessions — aucune lecture de compte, aucun ordre.
    """
    resultats = {}
    for nom, demo in (("démonstration", True), ("réel", False)):
        client = CapitalCom(cle, identifiant, mot_de_passe, demo=demo)
        try:
            client.ouvrir_session()
            resultats[nom] = {"ok": True, "base": client.base, "detail": ""}
        except BrokerError as e:
            resultats[nom] = {"ok": False, "base": client.base, "detail": str(e)}
    return resultats
