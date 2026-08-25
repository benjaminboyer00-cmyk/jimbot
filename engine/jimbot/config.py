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
]

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
