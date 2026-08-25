"""Chaîne de repli entre fournisseurs crypto. Aucun accès réseau : les
fournisseurs sont remplacés par des doublures.

Contexte : `api.binance.com` renvoie HTTP 451 depuis les runners GitHub, dont
les adresses IP américaines sont géo-bloquées par Binance. Le défaut est
invisible en local et supprime en production la totalité des actifs crypto.
"""
import numpy as np
import pandas as pd
import pytest

from jimbot.datasources import crypto
from jimbot.datasources.base import DataError


def _bougies(n: int = 200, prix: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = pd.Series(np.full(n, prix), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": np.ones(n)}, index=idx)


def test_le_premier_fournisseur_disponible_est_retenu(monkeypatch):
    appels = []

    def ok(nom, prix):
        def f(ref, itv, lim):
            appels.append(nom)
            return _bougies(prix=prix)
        return f

    monkeypatch.setattr(crypto, "PROVIDERS", [("a", ok("a", 1.0)), ("b", ok("b", 2.0))])
    df = crypto.klines("BTCUSDT")
    assert appels == ["a"], "le second fournisseur ne doit pas être sollicité"
    assert df["close"].iloc[-1] == 1.0


def test_bascule_sur_le_suivant_en_cas_d_echec(monkeypatch):
    """C'est exactement le scénario du 451 de Binance en production."""
    def geo_bloque(ref, itv, lim):
        raise DataError("451 Client Error")

    def secours(ref, itv, lim):
        return _bougies(prix=42.0)

    monkeypatch.setattr(crypto, "PROVIDERS",
                        [("binance", geo_bloque), ("secours", secours)])
    assert crypto.klines("BTCUSDT")["close"].iloc[-1] == 42.0


def test_une_exception_inattendue_ne_stoppe_pas_la_chaine(monkeypatch):
    def casse(ref, itv, lim):
        raise RuntimeError("panne inattendue")

    monkeypatch.setattr(crypto, "PROVIDERS",
                        [("casse", casse), ("ok", lambda r, i, l: _bougies(prix=7.0))])
    assert crypto.klines("BTCUSDT")["close"].iloc[-1] == 7.0


def test_reponse_trop_courte_est_rejetee(monkeypatch):
    """Une réponse quasi vide n'est pas exploitable et doit faire basculer."""
    monkeypatch.setattr(crypto, "PROVIDERS", [
        ("court", lambda r, i, l: _bougies(n=5)),
        ("complet", lambda r, i, l: _bougies(n=200, prix=9.0)),
    ])
    assert crypto.klines("BTCUSDT")["close"].iloc[-1] == 9.0


def test_erreur_explicite_si_tous_echouent(monkeypatch):
    def echec(nom):
        def f(ref, itv, lim):
            raise DataError(f"{nom} indisponible")
        return f

    monkeypatch.setattr(crypto, "PROVIDERS", [("a", echec("a")), ("b", echec("b"))])
    with pytest.raises(DataError) as exc:
        crypto.klines("BTCUSDT")
    # Le message doit nommer chaque échec, sinon le diagnostic est impossible.
    assert "a indisponible" in str(exc.value)
    assert "b indisponible" in str(exc.value)


def test_le_fournisseur_retenu_est_trace(monkeypatch):
    monkeypatch.setattr(crypto, "PROVIDERS", [("kraken", lambda r, i, l: _bougies())])
    assert crypto.klines("BTCUSDT").attrs["provider"] == "kraken"


# --------------------------------------------------------------------------
# Nomenclatures propres à chaque place
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ref,base", [
    ("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("SOLUSDC", "SOL"),
    ("XRPBUSD", "XRP"), ("LINKUSD", "LINK"), ("DOGE", "DOGE"),
])
def test_extraction_de_la_devise_de_base(ref, base):
    assert crypto._base_of(ref) == base


def test_kraken_utilise_sa_nomenclature_historique():
    """Kraken nomme le bitcoin XBT et le dogecoin XDG."""
    assert crypto.KRAKEN_BASE["BTC"] == "XBT"
    assert crypto.KRAKEN_BASE["DOGE"] == "XDG"


def test_la_chaine_reelle_contient_une_source_non_geobloquee():
    """`data-api.binance.vision` doit être essayé avant l'API principale :
    c'est le seul endpoint au format Binance qui ne soit pas géo-bloqué."""
    noms = [n for n, _ in crypto.PROVIDERS]
    assert noms[0] == "binance.vision"
    assert "coinbase" in noms and "kraken" in noms
