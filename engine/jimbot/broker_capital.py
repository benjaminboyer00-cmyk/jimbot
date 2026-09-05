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

import requests

from .broker import BrokerError, Compte, diagnostic
from .config import _env
from .datasources.base import DataError, http_post_json

log = logging.getLogger("jimbot.broker.capital")

BASE_DEMO = "https://demo-api-capital.backend-capital.com"
BASE_REEL = "https://api-capital.backend-capital.com"

# Termes de recherche par instrument interne. Capital.com nomme ses marchés
# librement ; on cherche, puis on retient la correspondance la plus plausible.
RECHERCHE: dict[str, list[str]] = {
    "XAUUSD": ["Gold"],
    "EURUSD": ["EUR/USD"],
    "GBPUSD": ["GBP/USD"],
    "USDJPY": ["USD/JPY"],
    "SPX": ["US 500", "S&P 500"],
    "NDX": ["US Tech 100", "Nasdaq"],
    "DXY": ["US Dollar Index", "Dollar Index"],
    "VIX": ["Volatility Index", "VIX"],
    "BTC-USD": ["Bitcoin"],
    "ETH-USD": ["Ethereum"],
    "SOL-USD": ["Solana"],
    "BNB-USD": ["Binance Coin", "BNB"],
    "XRP-USD": ["XRP"],
    "DOGE-USD": ["Dogecoin"],
    "AVAX-USD": ["Avalanche"],
    "LINK-USD": ["Chainlink"],
}


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
            raise BrokerError(
                f"session refusée (HTTP {r.status_code}). Vérifiez la clé d'API, "
                f"l'identifiant et le mot de passe. La clé doit être créée dans "
                f"Réglages > Intégrations API, avec l'authentification à deux "
                f"facteurs active.")

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
        epic = self.resoudre_epic_direct(symbole)
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
    def resoudre_epic_direct(self, terme: str) -> str | None:
        """Cherche un marché par nom et renvoie son epic."""
        try:
            d = self._get(f"/api/v1/markets?searchTerm={terme}")
        except BrokerError:
            return None
        for m in d.get("markets") or []:
            if m.get("marketStatus") == "TRADEABLE" and m.get("epic"):
                return str(m["epic"])
        return None

    def resoudre(self, symbole_interne: str) -> str | None:
        """Epic correspondant à un instrument du moteur, ou None."""
        if symbole_interne in self._cache_epic:
            return self._cache_epic[symbole_interne]
        epic = None
        for terme in RECHERCHE.get(symbole_interne, [symbole_interne]):
            epic = self.resoudre_epic_direct(terme)
            if epic:
                break
        self._cache_epic[symbole_interne] = epic
        return epic

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
