"""Configuration centrale : univers d'actifs, paramètres de risque, chemins."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"


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
    Asset("VIX", "yahoo", "^VIX", "index", "VIX"),
]

# Les memecoins ne sont pas listés en dur : ils sont découverts dynamiquement
# via DexScreener puis filtrés (voir datasources/dexscreener.py).
MEMECOIN_CHAINS = ["solana", "base"]


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
    signal_threshold: float = float(os.getenv("JIMBOT_SIGNAL_THRESHOLD", "58"))
    # Un signal n'est publié sur Discord qu'au-delà de ce score.
    alert_threshold: float = float(os.getenv("JIMBOT_ALERT_THRESHOLD", "68"))
    # Score au-delà duquel on ping un rôle Discord.
    ping_threshold: float = float(os.getenv("JIMBOT_PING_THRESHOLD", "80"))
    # Capital de départ du portefeuille papier.
    paper_capital: float = float(os.getenv("JIMBOT_PAPER_CAPITAL", "10000"))
    # Anti-spam : pas de nouvelle alerte sur le même actif avant N minutes.
    alert_cooldown_min: int = int(os.getenv("JIMBOT_ALERT_COOLDOWN_MIN", "180"))
    # Nombre de bougies chargées par actif.
    lookback: int = int(os.getenv("JIMBOT_LOOKBACK", "400"))

    discord_webhook: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    discord_role_id: str = field(default_factory=lambda: os.getenv("DISCORD_ROLE_ID", ""))
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    dry_run: bool = field(default_factory=lambda: os.getenv("JIMBOT_DRY_RUN", "") == "1")


SETTINGS = Settings()
