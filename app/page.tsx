/**
 * Tableau de bord. Rendu côté serveur à chaque requête : les fichiers JSON
 * sont réécrits par GitHub Actions puis committés, ce qui déclenche un
 * redéploiement — le dashboard reflète donc toujours le dernier scan.
 */
import {
  getLastReport,
  getSnapshot,
  getTrades,
  fmtCompact,
  fmtNum,
  fmtPrice,
  timeAgo,
  REGIME_LABELS,
  type Signal,
} from "@/lib/data";

import {
  ApiSection,
  Memecoins,
  NewsSummary,
  Reports,
  TradeJournal,
  Watchlist,
  WorldContext,
} from "./sections";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [snap, trades, report] = await Promise.all([
    getSnapshot(),
    getTrades(),
    getLastReport(),
  ]);

  if (!snap) {
    return (
      <>
        <Topbar stamp="en attente du premier scan" />
        <main className="wrap">
          <section>
            <div className="empty">
              Aucune donnée pour l’instant. Lancez le workflow «&nbsp;Scan de
              marché&nbsp;» dans l’onglet Actions, ou exécutez{" "}
              <code>python engine/scan.py</code> en local.
            </div>
          </section>
        </main>
      </>
    );
  }

  const { portfolio, counts } = snap;
  const perf = computePerf(trades, portfolio.initial);
  const actionable = snap.signals.filter((s) => s.direction !== "neutre");
  const ret = portfolio.initial ? (portfolio.equity / portfolio.initial - 1) * 100 : 0;

  return (
    <>
      <Topbar stamp={`dernier scan ${timeAgo(snap.generated_at)}`} />
      <main className="wrap">
        <div className="kpis">
          <Kpi label="Signaux actifs" value={String(counts.actionable)} />
          <Kpi label="Achat" value={String(counts.long)} tone={counts.long ? "up" : undefined} />
          <Kpi label="Vente" value={String(counts.short)} tone={counts.short ? "down" : undefined} />
          <Kpi label="Capital papier" value={fmtNum(portfolio.equity, 0)} />
          <Kpi
            label="Performance"
            value={`${ret >= 0 ? "+" : ""}${fmtNum(ret)} %`}
            tone={ret > 0 ? "up" : ret < 0 ? "down" : undefined}
          />
          <Kpi label="Trades fermés" value={String(perf.trades)} />
        </div>

        {snap.risk_off && snap.risk_off.count > 0 && (
          <WorldContext riskOff={snap.risk_off} />
        )}

        <NewsSummary
          summary={snap.news_summary ?? ""}
          engine={snap.news_engine}
          speeches={snap.speeches ?? []}
        />

        {report?.briefing && (
          <section>
            <h2>Briefing</h2>
            {report.briefing.split("\n\n").map((p, i) => (
              <p key={i} style={{ marginBottom: 10, maxWidth: "76ch" }}>
                {p}
              </p>
            ))}
            <p className="note">
              Rédigé {timeAgo(report.generated_at)} · moteur&nbsp;: {report.engine}
            </p>
          </section>
        )}

        <section>
          <h2>Configurations retenues</h2>
          {actionable.length ? (
            <div className="cards">
              {actionable.map((s) => (
                <SignalCard key={s.symbol} s={s} />
              ))}
            </div>
          ) : (
            <div className="empty">
              Aucune configuration n’atteint le seuil de conviction. Le moteur
              reste à l’écart — l’absence de signal en est un.
            </div>
          )}
        </section>

        <Watchlist items={snap.watchlist ?? []} />

        <section>
          <h2>Univers suivi · {counts.analysed} actifs</h2>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Symbole</th>
                  <th>Actif</th>
                  <th style={{ width: 170 }}>Conviction</th>
                  <th className="num">Score</th>
                  <th className="num">Prix</th>
                  <th>Régime</th>
                  <th className="num">Volatilité</th>
                  <th className="num">Presse</th>
                </tr>
              </thead>
              <tbody>
                {[...snap.signals]
                  .sort((a, b) => signed(b) - signed(a))
                  .map((s) => {
                    const v = signed(s);
                    return (
                      <tr key={s.symbol}>
                        <td style={{ fontWeight: 600 }}>{s.symbol}</td>
                        <td className="muted">{s.label}</td>
                        <td>
                          <div className="scorebar">
                            <i
                              className={v >= 0 ? "pos" : "neg"}
                              style={{ width: `${Math.abs(v) / 2}%` }}
                            />
                          </div>
                        </td>
                        <td className={`num ${v > 0 ? "up" : v < 0 ? "down" : ""}`}>
                          {v > 0 ? "+" : ""}
                          {v.toFixed(0)}
                        </td>
                        <td className="num">{fmtPrice(s.price)}</td>
                        <td className="muted">{REGIME_LABELS[s.regime.name] ?? s.regime.name}</td>
                        <td className="num muted">{fmtNum(s.atr_pct)} %</td>
                        <td className="num muted">
                          {s.news_count ? (
                            <span className={s.news_score > 0 ? "up" : s.news_score < 0 ? "down" : ""}>
                              {s.news_score > 0 ? "+" : ""}
                              {fmtNum(s.news_score)}
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
          <p className="note">
            Le score est signé&nbsp;: positif à l’achat, négatif à la vente. Un
            signal n’est émis qu’au-delà de 58 en valeur absolue, et publié sur
            Discord au-delà de 68.
          </p>
        </section>

        <section>
          <h2>Portefeuille papier</h2>
          <div className="kpis">
            <Kpi label="Trades" value={String(perf.trades)} />
            <Kpi label="Réussite" value={perf.trades ? `${fmtNum(perf.winRate, 0)} %` : "—"} />
            <Kpi
              label="Facteur profit"
              value={perf.profitFactor === null ? "—" : fmtNum(perf.profitFactor)}
              tone={perf.profitFactor && perf.profitFactor > 1 ? "up" : perf.profitFactor ? "down" : undefined}
            />
            <Kpi
              label="Espérance"
              value={perf.trades ? `${perf.expectancy >= 0 ? "+" : ""}${fmtNum(perf.expectancy)} R` : "—"}
              tone={perf.expectancy > 0 ? "up" : perf.expectancy < 0 ? "down" : undefined}
            />
            <Kpi label="Risque engagé" value={fmtNum(portfolio.open_risk)} />
            <Kpi label="Positions" value={String(portfolio.positions.length)} />
          </div>

          {portfolio.positions.length > 0 && (
            <>
              <h3 style={{ marginTop: 24 }}>Positions ouvertes</h3>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Symbole</th>
                      <th>Sens</th>
                      <th className="num">Entrée</th>
                      <th className="num">Stop</th>
                      <th className="num">Objectif</th>
                      <th className="num">Risque</th>
                      <th className="num">MFE / MAE</th>
                      <th>Stop</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio.positions.map((p) => (
                      <tr key={p.symbol}>
                        <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                        <td className={p.direction === "long" ? "up" : "down"}>
                          {p.direction === "long" ? "achat" : "vente"}
                        </td>
                        <td className="num">{fmtPrice(p.entry)}</td>
                        <td className="num">{fmtPrice(p.stop)}</td>
                        <td className="num">{fmtPrice(p.target)}</td>
                        <td className="num">{fmtNum(p.risk_amount)}</td>
                        <td className="num muted">
                          <span className="up">+{fmtNum(p.mfe)}</span> /{" "}
                          <span className="down">{fmtNum(p.mae)}</span>
                        </td>
                        <td className="muted">{p.stop_note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <TradeJournal trades={trades} />
        </section>

        <Reports reports={snap.reports ?? []} />

        <ApiSection />

        <Memecoins items={snap.memecoins} report={snap.meme_report ?? {}} />

        {snap.news.length > 0 && (
          <section>
            <h2>Actualités</h2>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th className="num">Score</th>
                    <th>Titre</th>
                    <th>Source</th>
                    <th>Actifs</th>
                    <th className="num">Âge</th>
                  </tr>
                </thead>
                <tbody>
                  {snap.news.slice(0, 24).map((n, i) => {
                    // Un article géopolitique porte une tension, pas un
                    // sentiment de marché : afficher « 0,0 » sur une dépêche de
                    // guerre la ferait passer pour neutre. On montre donc la
                    // mesure qui s'applique réellement à l'article.
                    const monde = n.category === "monde" && (n.risk ?? 0) !== 0;
                    const valeur = monde ? (n.risk ?? 0) : n.sentiment;
                    // Une tension positive est une escalade, donc défavorable
                    // au risque : le code couleur s'inverse par rapport au
                    // sentiment de marché.
                    const favorable = monde ? valeur < 0 : valeur > 0;
                    return (
                      <tr key={i}>
                        <td className="muted">
                          <span className="pill">{monde ? "tension" : "marché"}</span>
                        </td>
                        <td
                          className={`num ${
                            valeur === 0 ? "muted" : favorable ? "up" : "down"
                          }`}
                        >
                          {valeur > 0 ? "+" : ""}
                          {fmtNum(valeur, 1)}
                        </td>
                        <td className="wide">
                          {n.url ? (
                            <a href={n.url} target="_blank" rel="noopener noreferrer">
                              {n.title}
                            </a>
                          ) : (
                            n.title
                          )}
                        </td>
                        <td className="muted">{n.source}</td>
                        <td className="muted">
                          {(monde ? (n.risk_terms ?? []) : n.assets)
                            .slice(0, 2)
                            .map((a) => (
                              <span key={a} className="pill" style={{ marginRight: 4 }}>
                                {a}
                              </span>
                            ))}
                        </td>
                        <td className="num muted">{n.age_hours.toFixed(0)} h</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="note">
              Deux mesures distinctes selon la nature de l’article. Le{" "}
              <strong>sentiment de marché</strong> est positif quand la nouvelle
              est favorable à l’actif cité. La <strong>tension</strong> est
              positive lors d’une escalade géopolitique — ce qui est défavorable
              aux actifs de risque mais favorable à l’or et à la volatilité,
              d’où le code couleur inversé. Les deux sont calculés par lexique
              pondéré, en anglais et en français, avec inversion sur les
              tournures contextuelles («&nbsp;short liquidations&nbsp;» est
              haussier, une attaque «&nbsp;averted&nbsp;» n’est pas une
              escalade).
            </p>
          </section>
        )}

        <footer>
          Jimbot — analyse automatisée à but informatif. Ne constitue pas un
          conseil en investissement. Le portefeuille est simulé&nbsp;: aucun ordre
          réel n’est transmis. Les performances passées, réelles ou simulées, ne
          préjugent pas des performances futures.
          <br />
          Données&nbsp;: Binance, Yahoo Finance, DexScreener, flux RSS publics.
          Dernier scan&nbsp;: {new Date(snap.generated_at).toLocaleString("fr-FR")}.
        </footer>
      </main>
    </>
  );
}

/* ---------------------------------------------------------------- */

function Topbar({ stamp }: { stamp: string }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          JIMBOT<span>analyse de marché</span>
        </div>
        <div className="stamp">{stamp}</div>
      </div>
    </header>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div className="kpi">
      <div className={`kpi-value ${tone ?? ""}`}>{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}

function SignalCard({ s }: { s: Signal }) {
  const top = [...s.factors]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 4);
  const max = Math.max(...top.map((f) => Math.abs(f.contribution)), 0.01);

  return (
    <article className="card">
      <div className="card-head">
        <div>
          <div className="card-sym">{s.symbol}</div>
          <div className="card-name">{s.label}</div>
        </div>
        <span className={`badge ${s.direction}`}>
          {s.direction === "long" ? "ACHAT" : "VENTE"}
        </span>
      </div>

      <div className="gauge">
        <div className="gauge-track">
          <div className={`gauge-fill ${s.direction}`} style={{ width: `${s.score}%` }} />
        </div>
        <div className="gauge-meta">
          <span>conviction {s.score.toFixed(0)}/100</span>
          <span>{REGIME_LABELS[s.regime.name] ?? s.regime.name}</span>
        </div>
      </div>

      <div className="levels">
        <Level label="Entrée" value={fmtPrice(s.entry)} />
        <Level label="Stop" value={fmtPrice(s.stop)} />
        <Level label="Objectif" value={fmtPrice(s.target)} />
        <Level label="R/R" value={fmtNum(s.rr)} />
      </div>
      {s.stop_basis && (
        <div className="plan-basis">
          <div>
            <span className="muted">P(gain)</span> {(s.win_prob * 100).toFixed(0)} %{" · "}
            <span className="muted">espérance</span>{" "}
            <span className={s.expected_r > 0 ? "up" : "down"}>
              {s.expected_r >= 0 ? "+" : ""}
              {fmtNum(s.expected_r, 3)} R
            </span>
          </div>
          <div className="muted">stop ← {s.stop_basis}</div>
          <div className="muted">objectif ← {s.target_basis}</div>
        </div>
      )}

      <div className="factors">
        {top.map((f) => (
          <div className="factor" key={f.name}>
            <span className="factor-name">{f.name.replace("_", " ")}</span>
            <span className="factor-bar">
              <i
                className={f.contribution >= 0 ? "pos" : "neg"}
                style={{ width: `${(Math.abs(f.contribution) / max) * 50}%` }}
              />
            </span>
            <span className="factor-val">
              {f.contribution >= 0 ? "+" : ""}
              {(f.contribution * 100).toFixed(1)}
            </span>
          </div>
        ))}
      </div>

      {s.warnings.length > 0 && <div className="warn">{s.warnings.join(" · ")}</div>}
    </article>
  );
}

function Level({ label, value }: { label: string; value: string }) {
  return (
    <div className="level">
      <div className="level-label">{label}</div>
      <div className="level-value">{value}</div>
    </div>
  );
}

/** Score porteur de son sens : `score` est une magnitude non signée, et la
 *  direction n'est renseignée qu'au-delà du seuil. Pour un actif resté neutre,
 *  c'est le signe de `raw_score` qui porte l'orientation. */
function signed(s: Signal): number {
  if (s.direction === "short") return -s.score;
  if (s.direction === "long") return s.score;
  return s.raw_score >= 0 ? s.score : -s.score;
}

function computePerf(trades: { pnl: number; r_multiple: number }[], initial: number) {
  if (!trades.length) {
    return { trades: 0, winRate: 0, profitFactor: null as number | null, expectancy: 0, ret: 0 };
  }
  const wins = trades.filter((t) => t.pnl > 0);
  const grossWin = wins.reduce((a, t) => a + t.pnl, 0);
  const grossLoss = Math.abs(trades.filter((t) => t.pnl <= 0).reduce((a, t) => a + t.pnl, 0));
  return {
    trades: trades.length,
    winRate: (wins.length / trades.length) * 100,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : null,
    expectancy: trades.reduce((a, t) => a + t.r_multiple, 0) / trades.length,
    ret: initial ? (trades.reduce((a, t) => a + t.pnl, 0) / initial) * 100 : 0,
  };
}
