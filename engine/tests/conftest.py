"""Fixtures partagées. Aucun test ne touche au réseau."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ohlcv(close: np.ndarray, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = pd.Series(close, index=pd.date_range("2026-01-01", periods=len(close),
                                             freq="1h", tz="UTC"))
    wiggle = 1 + rng.uniform(0.001, 0.006, len(c))
    return pd.DataFrame({
        "open": c.shift(1).fillna(c.iloc[0]),
        "high": c * wiggle,
        "low": c / wiggle,
        "close": c,
        "volume": rng.lognormal(10, 0.4, len(c)),
    })


@pytest.fixture
def trending() -> pd.DataFrame:
    """Marché en tendance haussière nette."""
    rng = np.random.default_rng(1)
    return _ohlcv(100 * np.exp(np.cumsum(rng.normal(0.003, 0.008, 400))), 1)


@pytest.fixture
def ranging() -> pd.DataFrame:
    """Marché en retour à la moyenne (processus d'Ornstein-Uhlenbeck)."""
    rng = np.random.default_rng(2)
    x = np.zeros(400)
    for i in range(1, 400):
        x[i] = x[i - 1] * 0.75 + rng.normal(0, 0.01)
    return _ohlcv(100 * np.exp(x), 2)


@pytest.fixture
def random_walk() -> pd.DataFrame:
    """Marche aléatoire sans structure exploitable."""
    rng = np.random.default_rng(3)
    return _ohlcv(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))), 3)
