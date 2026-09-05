"""Garde-fous du raccordement courtier.

Ce module est le seul du dépôt qui puisse engager de l'argent. Les tests
portent donc d'abord sur ses refus, et seulement ensuite sur ce qu'il calcule.
"""
from __future__ import annotations

import pytest

from jimbot import broker as B


# Spécification d'un CFD ordinaire : un lot pour une unité de l'actif. Le
# contrat de 100 onces d'un vrai future sur l'or est traité à part, dans le
# test qui vérifie qu'on n'ouvre rien sous le lot minimal.
SPEC = {"contractSize": 1.0, "volumeStep": 0.01, "minVolume": 0.01,
        "maxVolume": 500.0, "digits": 2}


class FauxApi:
    """Compte de courtier simulé, entièrement sous contrôle du test."""

    def __init__(self, *, type_compte=B.DEMO, solde=10_000.0, positions=None,
                 connus=("XAUUSD", "BTCUSD"), trading=True, devise="USD",
                 prix=None):
        self._compte = B.Compte(
            login="123", serveur="Demo-Server", courtier="Courtier Test",
            devise=devise, solde=solde, equite=solde,
            type_compte=type_compte, trading_autorise=trading)
        self._positions = positions or []
        self._connus = set(connus)
        self._prix = prix or {}
        self.ordres: list[dict] = []

    def prix(self, symbole):
        return self._prix.get(symbole)

    def compte(self): return self._compte
    def positions(self): return self._positions
    def specification(self, s): return dict(SPEC) if s in self._connus else None

    def resoudre(self, interne):
        from jimbot import mt_symbols
        for a in mt_symbols.aliases(interne):
            if a in self._connus:
                return a
        return None

    def ordre(self, payload):
        self.ordres.append(payload)
        return {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE"}


def _signal(**kw) -> dict:
    base = {"symbol": "XAUUSD", "klass": "forex", "direction": "long",
            "actionable": True, "score": 65.0, "expected_r": 0.4,
            "entry": 4000.0, "stop": 3960.0, "target": 4080.0,
            "generated_at": "2026-09-05T17:50:41+00:00"}
    base.update(kw)
    return base


@pytest.fixture
def faux(monkeypatch):
    def _make(**kw):
        api = FauxApi(**kw)
        monkeypatch.setattr(B, "_client", lambda: api)
        return api
    return _make


# --------------------------------------------------------------------------
# Refus
# --------------------------------------------------------------------------
def test_refuse_un_compte_reel(faux, monkeypatch):
    """Le garde-fou principal : le type vient du courtier, pas d'un réglage."""
    monkeypatch.delenv("JIMBOT_BROKER_ALLOW_LIVE", raising=False)
    api = faux(type_compte="ACCOUNT_TRADE_MODE_REAL")
    r = B.synchroniser([_signal()])
    assert api.ordres == []
    assert "démonstration" in r["erreur"]


def test_compte_reel_exige_une_autorisation_explicite(faux, monkeypatch):
    monkeypatch.setenv("JIMBOT_BROKER_ALLOW_LIVE", "1")
    api = faux(type_compte="ACCOUNT_TRADE_MODE_REAL")
    B.synchroniser([_signal()])
    assert len(api.ordres) == 1, "l'autorisation explicite doit lever le refus"


def test_refuse_si_le_courtier_interdit_le_trading(faux):
    api = faux(trading=False)
    r = B.synchroniser([_signal()])
    assert api.ordres == [] and "refuse le trading" in r["erreur"]


def test_ignore_une_esperance_negative(faux):
    api = faux()
    r = B.synchroniser([_signal(expected_r=-0.05)])
    assert api.ordres == []
    assert r["ignores"][0]["raison"] == "espérance négative"


def test_ignore_un_signal_sous_le_seuil(faux):
    api = faux()
    B.synchroniser([_signal(actionable=False)])
    assert api.ordres == []


def test_ignore_un_instrument_absent_chez_le_courtier(faux):
    api = faux(connus=("EURUSD",))
    r = B.synchroniser([_signal(symbol="XAUUSD")])
    assert api.ordres == []
    assert "absent chez ce courtier" in r["ignores"][0]["raison"]


def test_n_ouvre_pas_deux_fois_le_meme_signal(faux):
    """Le scan réémet la même configuration à chaque passage tant qu'elle tient.

    Sans identifiant stable, une configuration qui dure une journée
    produirait quatre-vingt-seize positions identiques.
    """
    s = _signal()
    ouverte = {"symbol": "XAUUSD", "clientId": B.client_id(s), "volume": 0.1}
    api = faux(positions=[ouverte])
    r = B.synchroniser([s])
    assert api.ordres == []
    assert "déjà ouvert" in r["ignores"][0]["raison"]


def test_respecte_le_plafond_de_positions(faux, monkeypatch):
    monkeypatch.setattr(B, "MAX_POSITIONS", 1)
    api = faux(positions=[{"symbol": "EURUSD", "clientId": "autre"}])
    r = B.synchroniser([_signal()])
    assert api.ordres == []
    assert "plafond" in r["ignores"][0]["raison"]


def test_dry_run_ne_transmet_rien(faux):
    api = faux()
    r = B.synchroniser([_signal()], dry_run=True)
    assert api.ordres == []
    assert len(r["ordres"]) == 1 and r["ordres"][0]["simule"] is True


def test_un_courtier_injoignable_ne_fait_pas_echouer_le_scan(monkeypatch):
    def boom():
        raise B.BrokerError("réseau coupé")
    monkeypatch.setattr(B, "_client", boom)
    r = B.synchroniser([_signal()])
    assert r["actif"] is False and r["erreur"] == "réseau coupé"


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------
def test_volume_arrondit_vers_le_bas():
    """Arrondir vers le haut ferait dépasser le risque décidé — c'est
    exactement ce que tout le dimensionnement cherche à empêcher."""
    cent = {**SPEC, "contractSize": 100.0}
    # 1234 unités / contrat 100 = 12,34 lots, pas de 0,01.
    # La division entière sur flottants rendait 12,33 : 12,34 n'est pas
    # représentable exactement en binaire.
    assert B.volume_pour(cent, 1234.0) == pytest.approx(12.34)
    # 1239 unités = 12,39 lots ; avec un pas de 0,1 on descend à 12,3.
    gros_pas = {**cent, "volumeStep": 0.1}
    assert B.volume_pour(gros_pas, 1239.0) == pytest.approx(12.3)


def test_sous_le_lot_minimal_on_n_ouvre_rien():
    """Cas courant sur un petit compte, pas un cas limite.

    Avec 10 000 EUR, l'or à 4 000 et un stop à 40 points, le moteur dimensionne
    0,6 once. Sur un contrat de 100 onces cela fait 0,006 lot, sous le minimum
    de 0,01 : on n'ouvre rien plutôt que d'arrondir vers le haut et de risquer
    seize fois la somme prévue.
    """
    cent = {**SPEC, "contractSize": 100.0}
    assert B.volume_pour(cent, 0.6) == 0.0
    assert B.volume_pour(SPEC, 0.004) == 0.0


def test_un_petit_compte_ne_peut_pas_prendre_un_contrat_de_100_onces(faux):
    """Le refus doit être explicite et journalisé, pas un silence."""
    api = faux()
    api._connus = {"XAUUSD"}
    original = api.specification
    api.specification = lambda s: {**SPEC, "contractSize": 100.0} if s == "XAUUSD" else None
    r = B.synchroniser([_signal()])
    assert api.ordres == []
    assert "lot minimal" in r["ignores"][0]["raison"]


def test_volume_plafonne_au_maximum_du_courtier():
    assert B.volume_pour(SPEC, 1_000_000.0) == pytest.approx(SPEC["maxVolume"])


def test_volume_ordre_transmis_reste_sous_le_risque(faux):
    """La perte au stop du volume réellement transmis ne doit pas dépasser le
    risque calculé par le moteur."""
    api = faux(solde=10_000.0)
    s = _signal()
    B.synchroniser([s])
    assert len(api.ordres) == 1
    vol = api.ordres[0]["volume"]
    unites = vol * SPEC["contractSize"]
    perte = unites * abs(s["entry"] - s["stop"])

    from jimbot import risk as R
    prevu = R.position_size(10_000.0, s["entry"], s["stop"], s["klass"],
                            score=s["score"])
    assert perte <= prevu["risk_amount"] + 1e-6


def test_le_sens_et_les_niveaux_partent_correctement(faux):
    api = faux()
    B.synchroniser([_signal(direction="short", symbol="BTC-USD", klass="crypto",
                            entry=80_000.0, stop=81_000.0, target=78_000.0)])
    o = api.ordres[0]
    assert o["actionType"] == "ORDER_TYPE_SELL"
    assert o["symbol"] == "BTCUSD"
    assert o["stopLoss"] == 81_000.0 and o["takeProfit"] == 78_000.0
    assert o["comment"] == "jimbot"


def test_client_id_est_stable_sur_un_meme_scan():
    s = _signal()
    assert B.client_id(s) == B.client_id(dict(s))
    autre = _signal(generated_at="2026-09-05T19:10:00+00:00")
    assert B.client_id(s) != B.client_id(autre)


# --------------------------------------------------------------------------
# Conversion de devise : le volume doit risquer la somme décidée
# --------------------------------------------------------------------------
def test_meme_devise_aucune_conversion(faux):
    """Quand l'instrument est coté dans la devise du compte, rien ne change."""
    api = faux(devise="USD")
    api.specification = lambda s: {**SPEC, "profitCurrency": "USD"} if s in api._connus else None
    B.synchroniser([_signal()])
    assert len(api.ordres) == 1


def test_devise_de_cotation_differente_corrige_le_volume(faux):
    """Sur USDJPY, la distance au stop est en yens.

    Sans conversion, 141 unités à 0,567 ¥ font 80 ¥ — environ 0,51 $ — au lieu
    des 80 $ que le moteur croyait risquer : la position sortait cent
    cinquante-six fois trop petite.
    """
    api = faux(devise="USD", solde=100_000.0, connus=("USDJPY",),
               prix={"USDJPY": 156.0})
    spec_jpy = {**SPEC, "profitCurrency": "JPY", "contractSize": 100_000.0,
                "minVolume": 0.01, "volumeStep": 0.01, "digits": 3}
    api.specification = lambda s: dict(spec_jpy) if s == "USDJPY" else None

    s = _signal(symbol="USDJPY", klass="forex", entry=156.22, stop=155.65,
                target=157.36)
    B.synchroniser([s])

    assert len(api.ordres) == 1, "l'ordre était refusé faute de conversion"
    vol = api.ordres[0]["volume"]

    # La perte au stop, ramenée en devise du compte, doit valoir le risque décidé.
    from jimbot import risk as R
    prevu = R.position_size(100_000.0, s["entry"], s["stop"], "forex",
                            score=s["score"])["risk_amount"]
    perte_jpy = vol * spec_jpy["contractSize"] * abs(s["entry"] - s["stop"])
    perte_usd = perte_jpy / 156.0
    assert perte_usd <= prevu + 1e-6, "le volume risque plus que prévu"
    assert perte_usd > prevu * 0.5, "le volume risque bien moins que prévu"


def test_taux_inverse_quand_la_paire_est_dans_l_autre_sens(faux):
    api = faux(devise="EUR", prix={"EURUSD": 1.10})
    assert B.taux_vers_compte(api, "USD", "EUR") == pytest.approx(1 / 1.10)


def test_sans_taux_disponible_on_refuse_plutot_que_de_mal_dimensionner(faux):
    """Envoyer un volume dont on sait qu'il est faux est pire que ne rien
    envoyer — l'erreur peut aller vers le trop gros."""
    api = faux(devise="EUR", connus=("XAUUSD",), prix={})
    api.specification = lambda s: {**SPEC, "profitCurrency": "SGD"} if s == "XAUUSD" else None
    r = B.synchroniser([_signal()])
    assert api.ordres == []
    assert "conversion" in r["ignores"][0]["raison"]


# --------------------------------------------------------------------------
# Diagnostic : trois codes, trois causes, trois remèdes opposés
# --------------------------------------------------------------------------
def test_diagnostic_distingue_les_causes():
    """Un message générique envoie vérifier ce qui fonctionne déjà.

    Le cas rencontré en vrai : un 504 sur un jeton, un compte et une région
    parfaitement corrects, et le message invitait à vérifier les trois.
    """
    assert "jeton refusé" in B.diagnostic("HTTP 401 : ...")
    assert "compte introuvable" in B.diagnostic("HTTP 404 : ...")

    d504 = B.diagnostic("HTTP 504 : ...")
    assert "sont corrects" in d504, "un 504 ne doit pas accuser le jeton"
    assert "CONNECTED" in d504, "il doit dire où regarder"

    assert B.diagnostic("panne réseau") == "panne réseau"


def test_les_lectures_courtier_ne_sont_jamais_mises_en_cache(monkeypatch):
    """Une liste de positions périmée ferait rouvrir une position ouverte.

    Le cache de `http_get_json` dure quatre minutes — sain pour des bougies,
    dangereux pour l'état d'un compte.
    """
    vus = []

    def faux_get(url, params=None, **kw):
        vus.append(kw.get("cache"))
        return {}

    monkeypatch.setattr("jimbot.broker.http_get_json", faux_get)
    api = B.MetaApi(token="x", account_id="y")
    api._get("/positions")
    api._get("/account-information")
    assert vus == [False, False], f"cache actif sur une lecture courtier : {vus}"
