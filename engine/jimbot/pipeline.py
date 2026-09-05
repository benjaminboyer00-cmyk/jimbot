"""Orchestration : collecte, analyse, décision, publication.

Ce module est le seul à connaître l'ordre des opérations. Chaque étape est
isolée et tolérante aux pannes : la perte d'une source de données dégrade le
rapport, elle ne l'annule pas.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from . import calendar as cal
from . import history, ledger, risk, stats
from .config import MEMECOIN_CHAINS, REPORTS_DIR, RISK, SETTINGS, UNIVERSE, Asset
from .datasources import crypto, dexscreener, news as news_src, yahoo
from .datasources.base import DataError
from .paper import Portfolio, performance
from .store import now_iso, read, write, append_history, MAX_SIGNALS_HISTORY, MAX_CLOSED_TRADES
from .strategy import analyze

log = logging.getLogger("jimbot.pipeline")

# Unités de temps : analyse principale et confirmation supérieure.
TF_MAIN = "1h"
TF_HTF = {"binance": "4h", "yahoo": "1d"}


def load_candles(asset: Asset) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Charge l'unité de temps principale et l'unité supérieure d'un actif."""
    try:
        if asset.source == "binance":
            main = crypto.klines(asset.ref, TF_MAIN, SETTINGS.lookback)
            htf = crypto.klines(asset.ref, "4h", 300)
        else:
            main = yahoo.chart(asset.ref, TF_MAIN, SETTINGS.lookback)
            htf = yahoo.chart(asset.ref, "1d", 300)
        return main, htf
    except DataError as e:
        log.warning("%s ignoré : %s", asset.symbol, e)
        return None, None
    except Exception as e:  # noqa: BLE001 — un actif ne doit jamais tuer le scan
        log.error("%s : erreur inattendue %s", asset.symbol, e, exc_info=True)
        return None, None


def collect(universe: list[Asset] | None = None) -> dict:
    """Collecte parallèle des données de marché, des news et des memecoins."""
    universe = universe or UNIVERSE
    generated_at = now_iso()

    # Les news sont indépendantes des prix : on les charge en parallèle.
    with ThreadPoolExecutor(max_workers=8) as pool:
        news_future = pool.submit(_safe, news_src.fetch, [], "news")
        meme_future = pool.submit(_safe, dexscreener.screen_detailed, ([], {}),
                                  "memecoins", MEMECOIN_CHAINS, 12)
        candle_futures = {pool.submit(load_candles, a): a for a in universe}

        candles: dict[str, pd.DataFrame] = {}
        htfs: dict[str, pd.DataFrame] = {}
        for fut in as_completed(candle_futures):
            asset = candle_futures[fut]
            main, htf = fut.result()
            if main is not None and len(main) >= 40:
                candles[asset.symbol] = main
                if htf is not None:
                    htfs[asset.symbol] = htf

        articles = news_future.result()
        memecoins, meme_report = meme_future.result()

    # On passe l'univers complet : l'axe géopolitique ne cite jamais d'actif,
    # il doit néanmoins atteindre chacun d'eux via son bêta de valeur refuge.
    symbols = [a.symbol for a in universe]
    sentiment = news_src.sentiment_by_asset(articles, symbols) if articles else {}
    risk_off = news_src.risk_off_level(articles) if articles else {"level": 0.0, "count": 0, "top": []}
    monde = sum(1 for a in articles if a.category == "monde")
    log.info("collecte : %d/%d actifs, %d articles (%d monde), %d memecoin(s) sur %d criblé(s)",
             len(candles), len(universe), len(articles), monde, len(memecoins),
             meme_report.get("screened", 0))
    log.info("climat géopolitique : %+.3f sur %d article(s) porteurs",
             risk_off["level"], risk_off["count"])

    return {
        "generated_at": generated_at,
        "candles": candles,
        "htfs": htfs,
        "articles": articles,
        "sentiment": sentiment,
        "risk_off": risk_off,
        "memecoins": memecoins,
        "meme_report": meme_report,
        "universe": [a for a in universe if a.symbol in candles],
    }


def analyze_all(data: dict) -> list[dict]:
    """Analyse chaque actif disponible et renvoie les signaux sérialisés."""
    signals = []
    for asset in data["universe"]:
        sent = data["sentiment"].get(asset.symbol, {})
        try:
            sig = analyze(
                asset, data["candles"][asset.symbol], timeframe=TF_MAIN,
                df_htf=data["htfs"].get(asset.symbol),
                news_score=sent.get("score", 0.0), news_count=sent.get("count", 0),
                generated_at=data["generated_at"],
            )
            signals.append(sig.to_dict())
        except Exception as e:  # noqa: BLE001
            log.error("analyse de %s échouée : %s", asset.symbol, e, exc_info=True)
    return sorted(signals, key=lambda s: -s["score"])


def correlations(data: dict) -> pd.DataFrame:
    """Matrice de corrélation des actifs suivis."""
    closes = {sym: df["close"] for sym, df in data["candles"].items()}
    return stats.correlation_matrix(closes, n=90)


def run_paper(signals: list[dict], data: dict, corr: pd.DataFrame) -> tuple[dict, list[dict]]:
    """Fait vivre le portefeuille papier : sorties, puis nouvelles entrées.

    L'ordre est important — on libère d'abord les positions clôturées, sinon
    les plafonds de risque bloqueraient des entrées légitimes.
    """
    pf = Portfolio(read("portfolio", {}))
    closed: list[dict] = []

    # 1. Mise à jour des positions existantes.
    for sym in {p.symbol for p in pf.positions}:
        df = data["candles"].get(sym)
        if df is None:
            continue
        for trade in pf.update(sym, df):
            closed.append(trade.to_dict())

    # 2. Fermeture sur inversion de signal : le motif d'entrée a disparu.
    by_symbol = {s["symbol"]: s for s in signals}
    for pos in list(pf.positions):
        sig = by_symbol.get(pos.symbol)
        if sig and sig["direction"] != "neutre" and sig["direction"] != pos.direction:
            price = float(data["candles"][pos.symbol]["close"].iloc[-1])
            closed += [t.to_dict() for t in pf.close_symbol(pos.symbol, price, "inversion")]

    # 3. Nouvelles entrées, par ordre de conviction décroissante.
    fermes = read("trades", []) or []
    perf = performance(fermes, pf.equity_curve, pf.initial)
    kelly = perf.get("kelly") if perf.get("trades", 0) >= 30 else None

    marks = {sym: float(df["close"].iloc[-1]) for sym, df in data["candles"].items()}
    equity = pf.equity(marks)
    opened = []

    for sig in signals:
        if sig["direction"] == "neutre" or sig["score"] < SETTINGS.signal_threshold:
            continue
        if sig["stop"] <= 0:
            continue
        sizing = risk.position_size(equity, sig["entry"], sig["stop"], sig["klass"],
                                    score=sig["score"], kelly=kelly)
        candidate = {"symbol": sig["symbol"], "klass": sig["klass"],
                     "direction": sig["direction"], "risk_amount": sizing["risk_amount"]}
        allowed, reason = risk.portfolio_gate(
            [p.to_dict() for p in pf.positions], candidate, equity, corr)
        if not allowed:
            log.info("%s refusé par le contrôle de risque : %s", sig["symbol"], reason)
            sig.setdefault("warnings", []).append(f"non pris en portefeuille : {reason}")
            continue

        class _S:  # adaptateur minimal vers l'API de Portfolio.open
            pass
        s = _S()
        for k in ("symbol", "label", "klass", "direction", "price", "stop", "target",
                  "score", "regime"):
            setattr(s, k, sig[k])
        pos = pf.open(s, sizing)
        if pos:
            opened.append(pos.to_dict())
            sig["sizing"] = sizing

    pf.mark(marks)
    state = pf.to_dict(marks)
    write("portfolio", state)
    if closed:
        append_history("trades", closed, MAX_CLOSED_TRADES)
    log.info("portefeuille : %d ouverte(s), %d fermée(s) ce cycle, equity %.2f",
             len(pf.positions), len(closed), state["equity"])
    return state, closed


def persist_scan(signals: list[dict], data: dict, portfolio: dict,
                 closed: list[dict]) -> dict:
    """Écrit l'instantané consommé par le dashboard et l'historique."""
    regimes: dict[str, int] = {}
    for s in signals:
        regimes[s["regime"]["name"]] = regimes.get(s["regime"]["name"], 0) + 1

    from . import narrator  # import tardif : évite un cycle au chargement

    articles = [a.to_dict() for a in data["articles"]]
    speeches = news_src.major_speeches(data["articles"])
    agenda = cal.upcoming(articles)
    news_summary, news_engine = narrator.narrate_news(
        articles, data["sentiment"], data.get("risk_off", {}), speeches, agenda)

    # Liste de surveillance : les meilleures orientations du moment, y compris
    # sous le seuil de déclenchement. Sans elle, un jour calme n'affiche rien,
    # alors que l'information « voici ce qui s'en rapproche le plus » a de la
    # valeur.
    # Classées de la moins défavorable à la plus défavorable : la question
    # utile n'est pas « laquelle est bonne » — aucune ne l'est — mais « laquelle
    # l'est le moins ».
    watchlist = sorted(
        (s for s in signals if s["bias"] != "neutre" and not s["actionable"]),
        key=lambda s: -s["expected_r"])[:8]

    snapshot = {
        "generated_at": data["generated_at"],
        # Seuils en vigueur pour *ce* scan. Le dashboard les affichait en dur ;
        # ils sont réglables par variable d'environnement, si bien qu'un
        # changement de seuil laissait le site annoncer l'ancien.
        "seuils": {
            "signal": SETTINGS.signal_threshold,
            "alerte": SETTINGS.alert_threshold,
            "ping": SETTINGS.ping_threshold,
        },
        # Préréglages de risque, par classe d'actif. Le dashboard en a besoin
        # pour dimensionner une position sur le capital de son lecteur — un
        # calcul que le moteur ne peut pas faire, puisqu'il ne connaît que son
        # portefeuille papier. Les publier plutôt que les recopier côté site
        # garantit qu'il n'existe qu'une seule règle de dimensionnement.
        "risque": {
            "par_classe": {
                klass: {
                    "risque_pct": p.risk_pct,
                    "notionnel_max_pct": p.max_notional_pct,
                    "positions_max": p.max_positions,
                    "stop_atr": p.atr_stop_mult,
                    "rr_cible": p.rr_target,
                }
                for klass, p in RISK.items()
            },
            "risque_portefeuille_max": risk.MAX_PORTFOLIO_RISK,
            "risque_correle_max": risk.MAX_CORRELATED_RISK,
        },
        "news_summary": news_summary,
        "news_engine": news_engine,
        "speeches": speeches,
        "watchlist": watchlist,
        "agenda": agenda,
        "reports": list_reports(),
        "signals": signals,
        "regimes": regimes,
        "memecoins": [m.to_dict() | {"health_score": m.health_score}
                      for m in data["memecoins"]],
        "meme_report": data.get("meme_report", {}),
        "news": articles[:40],
        "sentiment": data["sentiment"],
        "risk_off": data.get("risk_off", {}),
        "portfolio": portfolio,
        "closed_this_cycle": closed,
        "counts": {
            "analysed": len(signals),
            "actionable": sum(1 for s in signals if s["direction"] != "neutre"),
            "long": sum(1 for s in signals if s["direction"] == "long"),
            "short": sum(1 for s in signals if s["direction"] == "short"),
        },
    }
    write("latest", snapshot)

    # Historique des signaux : uniquement les exploitables, pour rester léger.
    actionable = [{k: s[k] for k in ("symbol", "label", "klass", "direction", "score",
                                     "price", "entry", "stop", "target", "rr",
                                     "timeframe", "generated_at")}
                  | {"regime": s["regime"]["name"]}
                  for s in signals if s["direction"] != "neutre"]
    emis = read("signals", []) or []
    if actionable:
        emis = append_history("signals", actionable, MAX_SIGNALS_HISTORY)

    # Mémoire : la trajectoire de *tous* les actifs, signal ou non. C'est ce
    # qui permet de tracer un actif dans le temps plutôt que le seul présent.
    history.enregistrer(signals, data["generated_at"])

    # Redevabilité : ce que le marché a fait après chaque signal émis. Le
    # calcul a lieu ici parce que c'est le seul endroit où l'on dispose encore
    # des bougies — le dashboard, lui, ne voit que des fichiers JSON.
    ledger.enregistrer(emis, data["candles"], data["generated_at"])
    return snapshot


def list_reports() -> list[dict]:
    """Index des rapports PDF disponibles, du plus récent au plus ancien.

    Sert au dashboard, qui n'a aucun autre moyen de savoir quels rapports
    existent : il lit les données depuis le dépôt et ne voit pas le système de
    fichiers du moteur.
    """
    if not REPORTS_DIR.exists():
        return []
    out = []
    for f in sorted(REPORTS_DIR.glob("jimbot-*.pdf"), reverse=True):
        try:
            stat = f.stat()
        except OSError:
            continue
        out.append({
            "name": f.name,
            "date": f.stem.replace("jimbot-", ""),
            "size_kb": round(stat.st_size / 1024),
            "path": f"reports/{f.name}",
        })
    return out[:60]


def _safe(fn, default, label: str, *args):
    """Exécute une collecte optionnelle sans jamais propager d'exception."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.error("collecte %s échouée : %s", label, e)
        return default
