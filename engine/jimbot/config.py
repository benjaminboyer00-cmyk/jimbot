"""Configuration centrale : univers d'actifs, paramètres de risque, chemins."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


def _env(name: str, default: str) -> str:
    """Lit une variable d'environnement en traitant le vide comme absent.

    GitHub Actions exporte `FOO: ${{ vars.FOO }}` comme une chaîne **vide**
    lorsque la variable de dépôt n'est pas définie — la variable existe donc,
    et `os.getenv(name, default)` renvoie "" au lieu du défaut. `float("")`
    lève alors une ValueError et l'exécution échoue entièrement.
    """
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        # Une valeur mal saisie ne doit pas interrompre un scan : on retombe
        # sur le défaut en le signalant.
        logging.getLogger("jimbot.config").warning(
            "%s='%s' illisible, valeur par défaut %s retenue",
            name, os.getenv(name), default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))



@dataclass(frozen=True)
class Asset:
    """Un instrument suivi par le moteur.

    symbol   : identifiant interne, unique (ex. "BTC-USD")
    source   : datasource qui sait le charger ("binance", "yahoo", "dexscreener")
    ref      : identifiant natif chez la source (ex. "BTCUSDT", "EURUSD=X")
    klass    : classe d'actif, pilote le préréglage de risque
    label    : nom lisible pour les rapports
    """

    symbol: str
    source: str
    ref: str
    klass: str
    label: str


UNIVERSE: list[Asset] = [
    # --- Crypto majeures (Binance, OHLCV gratuit et fiable) ---
    Asset("BTC-USD", "binance", "BTCUSDT", "crypto", "Bitcoin"),
    Asset("ETH-USD", "binance", "ETHUSDT", "crypto", "Ethereum"),
    Asset("SOL-USD", "binance", "SOLUSDT", "crypto", "Solana"),
    Asset("BNB-USD", "binance", "BNBUSDT", "crypto", "BNB"),
    Asset("XRP-USD", "binance", "XRPUSDT", "crypto", "XRP"),
    Asset("DOGE-USD", "binance", "DOGEUSDT", "crypto", "Dogecoin"),
    Asset("AVAX-USD", "binance", "AVAXUSDT", "crypto", "Avalanche"),
    Asset("LINK-USD", "binance", "LINKUSDT", "crypto", "Chainlink"),
    # --- Forex (Yahoo, bougies journalières/horaires gratuites) ---
    Asset("EURUSD", "yahoo", "EURUSD=X", "forex", "Euro / Dollar"),
    Asset("GBPUSD", "yahoo", "GBPUSD=X", "forex", "Livre / Dollar"),
    Asset("USDJPY", "yahoo", "USDJPY=X", "forex", "Dollar / Yen"),
    Asset("XAUUSD", "yahoo", "GC=F", "forex", "Or / Dollar"),
    # --- Indices & actions ---
    Asset("SPX", "yahoo", "^GSPC", "index", "S&P 500"),
    Asset("NDX", "yahoo", "^IXIC", "index", "Nasdaq Composite"),
    Asset("DXY", "yahoo", "DX-Y.NYB", "index", "Dollar Index"),
    # --- Secteurs américains (ETF SPDR) ---
    #
    # Onze fonds qui découpent le S&P 500 en secteurs, et qui répondent à une
    # question que l'univers ne savait pas poser : *où* l'argent va, plutôt que
    # si le marché monte. Un secteur qui parcourt deux fois sa journée
    # ordinaire pendant que les dix autres dorment est une information ; le
    # même mouvement sur l'indice d'ensemble est invisible.
    #
    # Ils sont suivis comme n'importe quel actif — mêmes facteurs, même
    # calibrage. Rien n'est supposé de leur pouvoir prédictif : la sonde le
    # mesurera comme elle a mesuré le reste.
    Asset("XLK", "yahoo", "XLK", "secteur", "Technologie"),
    Asset("XLF", "yahoo", "XLF", "secteur", "Finance"),
    Asset("XLE", "yahoo", "XLE", "secteur", "Énergie"),
    Asset("XLV", "yahoo", "XLV", "secteur", "Santé"),
    Asset("XLI", "yahoo", "XLI", "secteur", "Industrie"),
    Asset("XLY", "yahoo", "XLY", "secteur", "Consommation discrétionnaire"),
    Asset("XLP", "yahoo", "XLP", "secteur", "Consommation de base"),
    Asset("XLU", "yahoo", "XLU", "secteur", "Services aux collectivités"),
    Asset("XLB", "yahoo", "XLB", "secteur", "Matériaux"),
    Asset("XLRE", "yahoo", "XLRE", "secteur", "Immobilier"),
    Asset("XLC", "yahoo", "XLC", "secteur", "Communication"),
    # --- Actions ---
    #
    # Une par secteur dominant, choisies sur la liquidité seule : ce sont les
    # plus échangées de leur secteur, donc celles dont le spread ne mange pas
    # l'avantage. Aucun choix n'est fait sur la valorisation ou la qualité du
    # bilan — le moteur ne sait pas encore lire un bilan, et prétendre le
    # contraire en piochant des titres « prometteurs » ne ferait qu'ajouter un
    # biais non mesuré.
    Asset("NVDA", "yahoo", "NVDA", "action", "Nvidia"),
    Asset("AAPL", "yahoo", "AAPL", "action", "Apple"),
    Asset("MSFT", "yahoo", "MSFT", "action", "Microsoft"),
    Asset("AMZN", "yahoo", "AMZN", "action", "Amazon"),
    Asset("GOOGL", "yahoo", "GOOGL", "action", "Alphabet"),
    Asset("META", "yahoo", "META", "action", "Meta"),
    Asset("TSLA", "yahoo", "TSLA", "action", "Tesla"),
    Asset("JPM", "yahoo", "JPM", "action", "JPMorgan"),
    Asset("XOM", "yahoo", "XOM", "action", "ExxonMobil"),
    Asset("LLY", "yahoo", "LLY", "action", "Eli Lilly"),
]

# Secteur de rattachement de chaque action, pour la rotation sectorielle.
# Écrit à la main plutôt que lu chez un fournisseur : la table tient en dix
# lignes, et l'endpoint de fondamentaux de Yahoo demande désormais une
# authentification.
SECTEUR_DE: dict[str, str] = {
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK",
    "AMZN": "XLY", "TSLA": "XLY",
    "GOOGL": "XLC", "META": "XLC",
    "JPM": "XLF", "XOM": "XLE", "LLY": "XLV",
}

# Actifs suivis pour le contexte uniquement : ils alimentent les corrélations
# et les bêtas de valeur refuge, mais ne donnent jamais lieu à un signal.
#
# Le VIX y figure sur la foi du backtest : 171 trades simulés, 10.5 % de
# réussite, -0.735 R d'espérance — de très loin le pire de l'univers. Le
# résultat est cohérent avec sa nature : un indice de volatilité alterne
# pics violents et affaissements lents, si bien qu'un moteur qui suit la
# tendance y achète systématiquement les sommets.
CONTEXT_ONLY: list[Asset] = [
    Asset("VIX", "yahoo", "^VIX", "index", "VIX"),
]

# Les memecoins sont découverts dynamiquement via DexScreener puis filtrés
# (voir datasources/dexscreener.py). Ils sont **criblés et affichés, jamais
# tradés**, et cette exclusion est délibérée pour trois raisons mesurées :
#
# 1. DexScreener ne fournit aucun historique de bougies. Sans OHLCV, il n'y a
#    ni ATR, ni régime, ni niveau structurel — donc aucun stop calculable et
#    aucun dimensionnement possible.
# 2. Leurs frais atteignent 125 points de base. Le coût rapporté au risque
#    vaut `frais × prix / distance_au_stop` : sur un stop de 2 %, cela fait
#    0.625 R, quand l'avantage mesuré sur l'univers principal vaut environ
#    0.03 R. L'espérance serait négative de deux ordres de grandeur.
# 3. Le filtre de survie retient un jeton par cycle en moyenne : la largeur
#    nécessaire à un avantage aussi mince n'existe pas.
#
# Le profil de risque « meme » de RISK reste défini pour le jour où une source
# d'historique existerait, mais il n'est référencé par aucun actif.
MEMECOIN_CHAINS = ["solana", "base"]
MEMECOINS_TRADABLES = False


@dataclass(frozen=True)
class RiskProfile:
    """Préréglages de risque par classe d'actif.

    atr_stop_mult    : distance du stop en multiples d'ATR(14)
    rr_target        : ratio rendement/risque visé pour le take-profit
    risk_pct         : fraction du capital risquée par trade (0.01 = 1 %)
    max_positions    : nombre max de positions simultanées sur cette classe
    max_notional_pct : exposition maximale d'une position, en fraction du capital

    `max_notional_pct` dépend de la classe parce qu'il protège du risque de
    gap — le saut de prix qui traverse le stop sans l'exécuter — et que ce
    risque est proportionnel à la volatilité. Un plafond unique calibré pour
    la crypto écraserait toutes les positions forex et indices, dont les stops
    sont dix fois plus serrés : à risque égal, un stop serré implique une
    position volumineuse, et le plafond mordrait systématiquement.
    """

    atr_stop_mult: float
    rr_target: float
    risk_pct: float
    max_positions: int
    max_notional_pct: float


# Multiplicateur global du risque par trade.
#
# Existe pour les petits comptes, et il faut être clair sur ce qu'il fait.
#
# Le moteur risque `risk_pct × conviction` par trade, soit 0,16 % du capital
# à un score de 58. Sur 50 €, cela fait huit centimes — une taille que plus
# aucun courtier n'accepte, et la plupart des instruments sont refusés avant
# même d'être envoyés. Le problème n'est pas le pourcentage, c'est que 50 €
# n'a pas la taille d'un compte de trading.
#
# On peut monter le risque pour rendre les ordres passables. Ce n'est pas
# gratuit, et l'arithmétique est simple : à 5 % par trade, vingt pertes
# consécutives suffisent à effacer le compte, et l'espérance mesurée du moteur
# est indiscernable de zéro (t = 1,13). Un multiplicateur élevé ne rend donc
# pas la stratégie meilleure, il rend la ruine plus rapide.
#
# Vaut 1 par défaut : un dépôt cloné ne doit jamais se mettre à risquer plus
# que ce que le moteur a mesuré.
#
# Lu à chaque appel et non à l'import. Une constante figée au chargement du
# module ne peut être ni testée ni ajustée sans redémarrer, et un réglage qu'on
# ne peut pas éprouver est un réglage dont on ignore s'il fonctionne — ce qui
# est intenable pour celui qui décide de combien on perd.
#
# Plafonné à 10. Au-delà, le risque par trade dépasse ce que le portefeuille
# papier, le rejeu et la sonde ont mesuré : le moteur ne décrirait plus la
# stratégie dont il publie les résultats.
RISK_MULT_MAX = 10.0


def risk_mult() -> float:
    """Multiplicateur courant du risque par trade, borné."""
    v = _env_float("JIMBOT_RISK_MULT", 1.0)
    if v <= 0:
        return 1.0
    return min(v, RISK_MULT_MAX)


RISK: dict[str, RiskProfile] = {
    "crypto": RiskProfile(atr_stop_mult=2.0, rr_target=2.0, risk_pct=0.010,
                          max_positions=5, max_notional_pct=0.25),
    # Les memecoins peuvent perdre 80 % en une bougie : exposition minimale.
    "meme": RiskProfile(atr_stop_mult=2.5, rr_target=3.0, risk_pct=0.004,
                        max_positions=3, max_notional_pct=0.05),
    "forex": RiskProfile(atr_stop_mult=1.8, rr_target=2.0, risk_pct=0.008,
                         max_positions=4, max_notional_pct=0.60),
    "index": RiskProfile(atr_stop_mult=2.2, rr_target=2.0, risk_pct=0.008,
                         max_positions=3, max_notional_pct=0.50),
    # Une action bouge plus qu'un indice et moins qu'une crypto ; son stop est
    # plus large que celui d'un indice parce qu'un titre isolé encaisse des
    # écarts de séance qu'un panier lisse.
    "action": RiskProfile(atr_stop_mult=2.5, rr_target=2.0, risk_pct=0.008,
                          max_positions=4, max_notional_pct=0.30),
    # Un ETF sectoriel est un panier : moins volatil qu'un titre, donc stop
    # plus serré et exposition plus large admissible.
    "secteur": RiskProfile(atr_stop_mult=2.0, rr_target=2.0, risk_pct=0.008,
                           max_positions=3, max_notional_pct=0.45),
}


@dataclass(frozen=True)
class Settings:
    """Réglages runtime, surchargeables par variables d'environnement."""

    # Un signal n'est émis qu'au-delà de ce score absolu (0-100).
    signal_threshold: float = field(default_factory=lambda: _env_float("JIMBOT_SIGNAL_THRESHOLD", 58.0))
    # Un signal n'est publié sur Discord qu'au-delà de ce score.
    alert_threshold: float = field(default_factory=lambda: _env_float("JIMBOT_ALERT_THRESHOLD", 68.0))
    # Score au-delà duquel on ping un rôle Discord.
    ping_threshold: float = field(default_factory=lambda: _env_float("JIMBOT_PING_THRESHOLD", 80.0))
    # Capital de départ du portefeuille papier.
    paper_capital: float = field(default_factory=lambda: _env_float("JIMBOT_PAPER_CAPITAL", 10000.0))
    # Anti-spam : pas de nouvelle alerte sur le même actif avant N minutes.
    alert_cooldown_min: int = field(default_factory=lambda: _env_int("JIMBOT_ALERT_COOLDOWN_MIN", 180))
    # Nombre de bougies chargées par actif.
    lookback: int = field(default_factory=lambda: _env_int("JIMBOT_LOOKBACK", 400))

    discord_webhook: str = field(default_factory=lambda: _env("DISCORD_WEBHOOK_URL", ""))
    discord_role_id: str = field(default_factory=lambda: _env("DISCORD_ROLE_ID", ""))
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    dry_run: bool = field(default_factory=lambda: os.getenv("JIMBOT_DRY_RUN", "") == "1")


SETTINGS = Settings()
