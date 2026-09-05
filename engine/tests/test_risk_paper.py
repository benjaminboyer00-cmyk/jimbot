"""Dimensionnement, plafonds de risque et exécution simulée.

C'est la partie du code qui décide combien on perd quand on a tort : elle est
testée sur ses cas limites, pas seulement sur son cas nominal.
"""
import numpy as np
import pandas as pd
import pytest

from jimbot import risk as R
from jimbot import paper as P
from jimbot.paper import Portfolio, Position, apply_costs, performance


# --------------------------------------------------------------------------
# Kelly
# --------------------------------------------------------------------------
def test_kelly_nul_sans_avantage():
    """35 % de réussite avec un gain de 1.5 pour 1 de perte est perdant."""
    assert R.kelly_fraction(0.35, 1.5, 1.0) == 0.0


def test_kelly_plafonne_a_25_pourcent():
    assert R.kelly_fraction(0.95, 5.0, 1.0) == pytest.approx(0.25)


def test_kelly_est_bien_un_demi_kelly():
    wr, w, l = 0.6, 2.0, 1.0
    plein = (2.0 * 0.6 - 0.4) / 2.0
    assert R.kelly_fraction(wr, w, l) == pytest.approx(plein * 0.5, abs=1e-9)


def test_kelly_robuste_aux_entrees_absurdes():
    assert R.kelly_fraction(0.0, 1.0, 1.0) == 0.0
    assert R.kelly_fraction(1.0, 1.0, 1.0) == 0.0
    assert R.kelly_fraction(0.5, 1.0, 0.0) == 0.0


# --------------------------------------------------------------------------
# Dimensionnement
# --------------------------------------------------------------------------
def test_le_risque_engage_correspond_a_la_distance_au_stop():
    """Propriété fondamentale : si le stop saute, on perd exactement le montant prévu."""
    p = R.position_size(10_000, 100.0, 98.0, "crypto", score=100)
    perte = p["units"] * (100.0 - 98.0)
    assert perte == pytest.approx(p["risk_amount"], rel=1e-6)


def test_un_stop_plus_serre_donne_une_position_plus_grosse_a_risque_egal():
    # Stops assez larges pour qu'aucun plafond de notionnel ne morde.
    large = R.position_size(10_000, 100.0, 90.0, "crypto", score=80)
    serre = R.position_size(10_000, 100.0, 96.0, "crypto", score=80)
    assert serre["units"] > large["units"]
    assert serre["risk_amount"] == pytest.approx(large["risk_amount"], rel=0.01)


def test_plafond_de_notionnel_sur_stop_tres_serre():
    """Un stop à 0.05 % produirait une position de plusieurs fois le capital."""
    from jimbot.config import RISK
    p = R.position_size(10_000, 100.0, 99.95, "crypto", score=100)
    assert p["notional"] <= 10_000 * RISK["crypto"].max_notional_pct + 0.01
    assert "plafonné" in p["reason"]


def test_plafond_de_notionnel_depend_de_la_classe():
    """Un plafond unique écraserait les positions forex, dont les stops sont
    dix fois plus serrés que ceux de la crypto."""
    from jimbot.config import RISK
    assert RISK["meme"].max_notional_pct < RISK["crypto"].max_notional_pct
    assert RISK["crypto"].max_notional_pct < RISK["forex"].max_notional_pct
    forex = R.position_size(10_000, 1.10, 1.0921, "forex", score=80)  # stop ~0.72 %
    assert forex["notional"] > 10_000 * RISK["crypto"].max_notional_pct


def test_conviction_module_la_taille():
    faible = R.position_size(10_000, 100.0, 98.0, "crypto", score=58)
    forte = R.position_size(10_000, 100.0, 98.0, "crypto", score=100)
    assert forte["risk_amount"] > faible["risk_amount"]


def test_kelly_ne_peut_que_reduire_le_risque():
    sans = R.position_size(10_000, 100.0, 98.0, "crypto", score=90)
    avec = R.position_size(10_000, 100.0, 98.0, "crypto", score=90, kelly=0.001)
    assert avec["risk_amount"] < sans["risk_amount"]
    genereux = R.position_size(10_000, 100.0, 98.0, "crypto", score=90, kelly=0.99)
    assert genereux["risk_amount"] == pytest.approx(sans["risk_amount"])


def test_stop_invalide_donne_taille_nulle():
    assert R.position_size(10_000, 100.0, 100.0, "crypto")["units"] == 0.0
    assert R.position_size(0, 100.0, 98.0, "crypto")["units"] == 0.0


# --------------------------------------------------------------------------
# Contrôle de portefeuille
# --------------------------------------------------------------------------
def _pos(sym, risque=100.0, sens="long", klass="crypto"):
    return {"symbol": sym, "klass": klass, "direction": sens, "risk_amount": risque}


def test_refus_si_position_deja_ouverte():
    ok, why = R.portfolio_gate([_pos("BTC-USD")], _pos("BTC-USD"), 10_000)
    assert not ok and "déjà ouverte" in why


def test_refus_au_dela_du_risque_total():
    # 4 positions seulement, pour ne pas heurter d'abord la limite par classe.
    positions = [_pos(f"A{i}", 200.0) for i in range(4)]
    ok, why = R.portfolio_gate(positions, _pos("NEW", 500.0), 10_000)
    assert not ok and "risque portefeuille" in why


def test_refus_au_dela_du_nombre_de_positions_par_classe():
    positions = [_pos(f"A{i}", 10.0) for i in range(5)]  # max crypto = 5
    ok, why = R.portfolio_gate(positions, _pos("NEW", 10.0), 100_000)
    assert not ok and "positions ouvertes" in why


def test_risque_correle_agrege():
    """Trois longs corrélés à 0.9 sont un seul pari de taille triple."""
    corr = pd.DataFrame([[1.0, 0.92], [0.92, 1.0]],
                        index=["BTC-USD", "ETH-USD"], columns=["BTC-USD", "ETH-USD"])
    positions = [_pos("ETH-USD", 300.0)]
    ok, why = R.portfolio_gate(positions, _pos("BTC-USD", 300.0), 10_000, corr)
    assert not ok and "corrélé" in why


def test_positions_opposees_sur_actifs_correles_autorisees():
    """Long BTC + short ETH corrélés positivement, ce n'est pas du risque cumulé."""
    corr = pd.DataFrame([[1.0, 0.92], [0.92, 1.0]],
                        index=["BTC-USD", "ETH-USD"], columns=["BTC-USD", "ETH-USD"])
    positions = [_pos("ETH-USD", 300.0, sens="short")]
    ok, _ = R.portfolio_gate(positions, _pos("BTC-USD", 300.0, sens="long"), 10_000, corr)
    assert ok


# --------------------------------------------------------------------------
# Stop suiveur
# --------------------------------------------------------------------------
def test_stop_suiveur_inactif_avant_un_r_de_profit():
    stop, note = R.trailing_stop(100, 100.5, 98, 1.0, "long")
    assert stop == 98 and note == "inchangé"


def test_stop_suiveur_remonte_apres_un_r():
    stop, note = R.trailing_stop(100, 103, 98, 1.0, "long")
    assert stop > 98 and note != "inchangé"


def test_stop_suiveur_ne_recule_jamais():
    """Un stop qui recule n'est pas un stop."""
    stop, _ = R.trailing_stop(100, 103, 101.9, 1.0, "long")
    assert stop >= 101.9


def test_stop_suiveur_symetrique_en_vente():
    stop, note = R.trailing_stop(100, 97, 102, 1.0, "short")
    assert stop < 102 and note != "inchangé"


# --------------------------------------------------------------------------
# Portefeuille papier
# --------------------------------------------------------------------------
def test_les_couts_degradent_toujours_le_prix():
    assert apply_costs(100, "crypto", "long", opening=True) > 100    # on achète plus cher
    assert apply_costs(100, "crypto", "long", opening=False) < 100   # on vend moins cher
    assert apply_costs(100, "crypto", "short", opening=True) < 100
    assert apply_costs(100, "crypto", "short", opening=False) > 100


def test_les_memecoins_coutent_plus_cher_que_le_forex():
    meme = apply_costs(100, "meme", "long", True) - 100
    forex = apply_costs(100, "forex", "long", True) - 100
    assert meme > forex * 5


class _Sig:
    symbol, label, klass = "BTC-USD", "Bitcoin", "crypto"
    direction, price, stop, target = "long", 100.0, 98.0, 104.0
    score, regime = 75.0, {"name": "tendance_haussière"}


def _candles(rows):
    idx = pd.date_range("2030-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, index=idx,
                        columns=["open", "high", "low", "close", "volume"])


def test_cible_atteinte_produit_un_gain():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    closed = pf.update("BTC-USD", _candles([
        [100, 101, 99.5, 101, 1], [101, 105, 100.5, 104, 1]]))
    assert len(closed) == 1
    assert closed[0].reason == "cible"
    assert closed[0].pnl > 0
    # Les coûts font que le R réalisé est légèrement sous le R théorique de 2.
    assert 1.7 < closed[0].r_multiple < 2.0


def test_stop_atteint_produit_une_perte():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    closed = pf.update("BTC-USD", _candles([[100, 100.5, 97, 97.5, 1]]))
    assert closed[0].reason == "stop"
    assert closed[0].pnl < 0


def test_stop_prioritaire_si_stop_et_cible_dans_la_meme_bougie():
    """Sans données infra-bougie, retenir la cible serait de l'auto-illusion."""
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    closed = pf.update("BTC-USD", _candles([[100, 105, 97, 102, 1]]))
    assert closed[0].reason == "stop"


def test_sortie_detectee_sur_les_meches_pas_sur_la_cloture():
    """Une mèche qui touche le stop puis se retourne doit clôturer la position."""
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    # La clôture (100.5) est au-dessus du stop, mais le plus-bas l'a touché.
    closed = pf.update("BTC-USD", _candles([[100, 101, 97.5, 100.5, 1]]))
    assert len(closed) == 1 and closed[0].reason == "stop"


def test_mfe_et_mae_suivies():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    pf.update("BTC-USD", _candles([[100, 103, 99, 101, 1]]))
    pos = pf.positions[0]
    assert pos.mfe > 0 and pos.mae < 0


def test_fermeture_sur_inversion():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    closed = pf.close_symbol("BTC-USD", 101.0, "inversion")
    assert closed[0].reason == "inversion" and not pf.positions


def test_serialisation_et_rechargement():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    state = pf.to_dict({"BTC-USD": 101.0})
    rechargé = Portfolio(state)
    assert len(rechargé.positions) == 1
    assert rechargé.positions[0].symbol == "BTC-USD"


def test_equity_inclut_le_latent():
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    pf.open(_Sig(), {"units": 50.0, "notional": 5_000.0, "risk_amount": 100.0})
    assert pf.equity({"BTC-USD": 110.0}) > pf.capital


# --------------------------------------------------------------------------
# Statistiques de performance
# --------------------------------------------------------------------------
def _trade(pnl, r, reason="cible"):
    return {"pnl": pnl, "r_multiple": r, "reason": reason, "klass": "crypto",
            "regime": "range", "bars_held": 10, "fees": 1.0, "symbol": "X",
            "direction": "long"}


def test_facteur_de_profit_et_esperance():
    trades = [_trade(200, 2.0), _trade(200, 2.0), _trade(-100, -1.0, "stop")]
    perf = performance(trades, [], 10_000)
    assert perf["profit_factor"] == pytest.approx(4.0)
    assert perf["expectancy_r"] == pytest.approx(1.0)
    assert perf["win_rate"] == pytest.approx(66.7, abs=0.1)


def test_series_consecutives():
    trades = [_trade(10, 1), _trade(10, 1), _trade(-5, -1), _trade(-5, -1),
              _trade(-5, -1), _trade(10, 1)]
    perf = performance(trades, [], 10_000)
    assert perf["max_win_streak"] == 2
    assert perf["max_loss_streak"] == 3


def test_performance_sans_trade():
    assert performance([], [], 10_000)["trades"] == 0


# --------------------------------------------------------------------------
# Durée de détention : un compteur, pas un cumul
# --------------------------------------------------------------------------
def _bougies_horaires(n: int, depart: str = "2026-09-01T00:00:00+00:00",
                      prix: float = 100.0) -> pd.DataFrame:
    """Série horaire plate : aucune position ne peut toucher stop ni objectif,
    seule l'expiration peut la fermer."""
    idx = pd.date_range(depart, periods=n, freq="h", tz="UTC")
    p = np.full(n, prix)
    return pd.DataFrame(
        {"open": p, "high": p, "low": p, "close": p, "volume": np.full(n, 1.0)},
        index=idx)


def test_bars_held_compte_les_bougies_pas_les_scans():
    """Le scan tourne quatre fois par heure et recharge toute la fenêtre.

    `new_bars` contient donc à chaque passage *toutes* les bougies depuis
    l'ouverture. En cumulant, le compteur croissait comme le carré du temps :
    treize heures de détention affichaient 128 bougies. Une position ouverte
    depuis dix bougies doit en compter dix, quel que soit le nombre de fois
    qu'on a rafraîchi entre-temps.
    """
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    bougies = _bougies_horaires(200)

    pos = Position(
        symbol="TEST", label="Test", klass="crypto", direction="long",
        entry=100.0, entry_ref=100.0, stop=95.0, target=110.0, units=10.0,
        notional=1_000.0, risk_amount=50.0,
        opened_at="2026-09-01T00:00:00+00:00", score=60.0, regime="range")
    pf.positions.append(pos)

    # Dix heures se sont écoulées, rafraîchies vingt fois — comme en production.
    fenetre = bougies.iloc[: 1 + 10]
    for _ in range(20):
        pf.update("TEST", fenetre)

    assert pos.bars_held == 10, (
        f"{pos.bars_held} bougies comptées pour 10 écoulées : le compteur cumule")


def test_expiration_survient_au_bon_horizon():
    """Une position ne doit pas expirer avant MAX_HOLD_BARS bougies réelles."""
    pf = Portfolio({"capital": 10_000, "initial": 10_000})
    bougies = _bougies_horaires(P.MAX_HOLD_BARS + 50)

    pos = Position(
        symbol="TEST", label="Test", klass="crypto", direction="long",
        entry=100.0, entry_ref=100.0, stop=95.0, target=110.0, units=10.0,
        notional=1_000.0, risk_amount=50.0,
        opened_at="2026-09-01T00:00:00+00:00", score=60.0, regime="range")
    pf.positions.append(pos)

    # Bien avant l'horizon, rafraîchi de nombreuses fois : rien ne doit fermer.
    avant = bougies.iloc[: 1 + P.MAX_HOLD_BARS - 10]
    for _ in range(40):
        assert pf.update("TEST", avant) == [], "fermeture prématurée"
    assert pf.positions, "la position a disparu avant son horizon"

    # Une fois l'horizon franchi, elle expire.
    apres = bougies.iloc[: 1 + P.MAX_HOLD_BARS + 1]
    fermes = pf.update("TEST", apres)
    assert len(fermes) == 1 and fermes[0].reason == "expiration"
