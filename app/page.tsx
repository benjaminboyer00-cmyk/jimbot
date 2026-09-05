/**
 * Tableau de bord. Rendu côté serveur à chaque requête : les fichiers JSON
 * sont réécrits par GitHub Actions puis committés, ce qui déclenche un
 * redéploiement — le dashboard reflète donc toujours le dernier scan.
 */
import {
  getBacktest,
  getProbe,
  getLastReport,
  getSnapshot,
  getSuivi,
  getTrades,
  fmtNum,
  fmtPrice,
  seuils,
  timeAgo,
  REGIME_LABELS,
  type Backtest,
  type Signal,
} from "@/lib/data";

import { serieEquite, cumulTradesPapier, bandeDuScore } from "@/lib/series";
import { reglagesRisque } from "@/lib/sizing";
import { Dimensionnement } from "@/components/dimensionnement";
import { Chart, CourbeCapital, CourbeCumulR, PlanTrade } from "@/components/charts";
import { Topbar } from "@/components/topbar";

import {
  Agenda,
  FactorPower,
  Validation,
  ApiSection,
  Kpi,
  Marche,
  Memecoins,
  NewsSummary,
  Redevabilite,
  Reports,
  Rotation,
  TradeJournal,
  Watchlist,
  WorldContext,
} from "./sections";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [snap, trades, report, backtest, probe, suivi] = await Promise.all([
    getSnapshot(),
    getTrades(),
    getLastReport(),
    getBacktest(),
    getProbe(),
    getSuivi(),
  ]);

  if (!snap) {
    return (
      <>
        <Topbar stamp="en attente du premier scan" actif="tableau" />
        <main className="wrap" id="contenu">
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
  const capital = serieEquite(portfolio);
  // Un plan dont les niveaux datent de plusieurs heures ne se traite pas tel
  // quel : la carte doit le dire avant d'afficher un prix d'entrée.
  const perime = (Date.now() - new Date(snap.generated_at).getTime()) / 60000 > 90;
  const cumul = cumulTradesPapier(trades);
  const seuil = seuils(snap);
  const risque = reglagesRisque(snap);

  return (
    <>
      <Topbar
        stamp={`dernier scan ${timeAgo(snap.generated_at)}`}
        generatedAt={snap.generated_at}
        actif="tableau"
      />
      <main className="wrap" id="contenu">
        <div className="masthead">
          <h1>Ce que le moteur a mesuré au dernier passage</h1>
          <p>
            Sept facteurs recalculés à la main sur {counts.analysed} actifs,
            quatre fois par heure. Chaque signal émis reste au tableau jusqu’à
            ce que le marché le tranche —{" "}
            <strong>y compris quand il a eu tort</strong>.
          </p>
        </div>

        <Staleness generatedAt={snap.generated_at} />

        <div className="kpis">
          <Kpi label="Signaux actifs" value={String(counts.actionable)} />
          <Kpi label="Achat" value={String(counts.long)} tone={counts.long ? "up" : undefined} />
          <Kpi label="Vente" value={String(counts.short)} tone={counts.short ? "down" : undefined} />
          <Kpi
            label="Capital papier"
            value={fmtNum(portfolio.equity, 0)}
            spark={capital.points.map((p) => p.v)}
            tone={ret > 0 ? "up" : ret < 0 ? "down" : undefined}
          />
          <Kpi
            label="Performance"
            value={`${ret >= 0 ? "+" : ""}${fmtNum(ret)} %`}
            tone={ret > 0 ? "up" : ret < 0 ? "down" : undefined}
          />
          <Kpi label="Trades fermés" value={String(perf.trades)} />
        </div>

        <Marche signaux={snap.signals} />

        <Rotation rotation={snap.rotation} />

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
            <div className="prose">
              {report.briefing.split("\n\n").map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
            <p className="note">
              Rédigé {timeAgo(report.generated_at)} · moteur&nbsp;: {report.engine}
            </p>
          </section>
        )}

        <section>
          <h2>Configurations retenues</h2>
          <p className="note" style={{ marginTop: 0, marginBottom: 14 }}>
            Convention de lecture&nbsp;: une valeur{" "}
            <span className="predit">soulignée en pointillé</span> est une{" "}
            <strong>prédiction du modèle</strong>&nbsp;; une valeur non
            soulignée est une <strong>mesure</strong>. Chaque carte porte, sous
            le plan, ce que sa tranche de score a réellement produit en rejeu —
            c&rsquo;est cette ligne-là qui dit ce que vaut la prédiction, pas la
            prédiction elle-même.
          </p>
          {actionable.length ? (
            <div className="cards">
              {actionable.map((s) => (
                <SignalCard key={s.symbol} s={s} bt={backtest} perime={perime} />
              ))}
            </div>
          ) : (
            <div className="empty">
              Aucune configuration n’atteint le seuil de conviction. Le moteur
              reste à l’écart — l’absence de signal en est un.
            </div>
          )}
        </section>

        {/* Le tableau se nourrissait des seules configurations retenues, et
            celles-ci sont rares par construction — 5,6 % des relevés franchissent
            le seuil. Le calculateur passait donc l'essentiel de son temps vide,
            alors que c'est un outil qui n'a aucune raison de dépendre d'un
            signal. À défaut de configuration retenue, il dimensionne les plans
            de la liste de surveillance, en disant clairement qu'ils sont sous le
            seuil. */}
        <Dimensionnement
          signaux={actionable.length ? actionable : (snap.watchlist ?? [])}
          horsSeuil={actionable.length === 0}
          reglages={risque}
          seuilSignal={seuil.signal}
        />

        <Redevabilite suivi={suivi} />

        <Agenda agenda={snap.agenda} />

        <Watchlist items={snap.watchlist ?? []} />

        <section>
          <h2>
            Univers suivi
            <span className="compte">{counts.analysed} actifs</span>
          </h2>
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
                        <td style={{ fontWeight: 600 }}>
                          <a href={`/actif/${encodeURIComponent(s.symbol)}`}>{s.symbol}</a>
                        </td>
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
            signal n’est émis qu’au-delà de {seuil.signal} en valeur absolue, et
            publié sur Discord au-delà de {seuil.alerte}. Cliquez un symbole
            pour son historique et les signaux qu’il a portés.
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

          <div className="charts" style={{ marginTop: 16 }}>
            <Chart
              wide
              title="Capital et repli"
              sub={`${capital.points.length} relevés depuis l’ouverture`}
              foot={
                <>
                  Un point par scan. Le trait pointillé est le capital de
                  départ, le panneau du bas la distance au plus haut atteint.
                  Le détail et les autres séries sont sur la page{" "}
                  <a href="/courbes">Courbes</a>.
                </>
              }
            >
              <CourbeCapital serie={capital} />
            </Chart>

            {trades.length > 1 && (
              <Chart
                wide
                title="R cumulés"
                sub={`${cumul.gagnants} gagnants · ${cumul.perdants} perdants`}
                foot={
                  trades.length < 30
                    ? "Moins de trente trades : la courbe décrit surtout du hasard. Elle est affichée pour ce qu’elle est, pas comme une preuve."
                    : "Somme des R des trades fermés, dans l’ordre de clôture."
                }
              >
                <CourbeCumulR serie={cumul} h={180} seed="accueil" />
              </Chart>
            )}
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

        <Validation bt={backtest ?? undefined} />

        <FactorPower probe={probe ?? undefined} />

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

/**
 * Avertit quand le dernier scan remonte à trop longtemps.
 *
 * L'ordonnanceur de GitHub Actions traite les planifications comme un service
 * « au mieux » et abandonne les exécutions en charge. Mesuré sur 71 heures
 * avec un cron toutes les 15 minutes : 4,2 % de couverture réelle et un écart
 * médian de 7 heures. Un tableau de bord qui n'en dit rien laisse croire à
 * une surveillance continue qui n'existe pas.
 */
function Staleness({ generatedAt }: { generatedAt: string }) {
  const minutes = (Date.now() - new Date(generatedAt).getTime()) / 60000;
  if (minutes <= 90) return null;
  const heures = Math.round(minutes / 60);
  return (
    <div className="warn" style={{ marginTop: 20, maxWidth: "76ch" }}>
      <strong>Données vieilles de {heures} h.</strong> Le scan est planifié
      toutes les heures et enchaîne quatre passages espacés de 15 minutes, mais
      l’ordonnanceur de GitHub Actions traite les planifications comme un
      service « au mieux » et abandonne des exécutions en période de charge.
      Les chiffres ci-dessous décrivent le marché tel qu’il était au dernier
      passage, pas tel qu’il est maintenant.
    </div>
  );
}

function SignalCard({
  s,
  bt,
  perime,
}: {
  s: Signal;
  bt: Backtest | null;
  perime: boolean;
}) {
  const top = [...s.factors]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 4);
  const max = Math.max(...top.map((f) => Math.abs(f.contribution)), 0.01);
  const bande = bandeDuScore(bt, s.score);

  return (
    <article className={`card${perime ? " perime" : ""}`}>
      <div className="card-head">
        <div>
          <div className="card-sym">{s.symbol}</div>
          <div className="card-name">{s.label}</div>
        </div>
        <span className={`badge ${s.direction}`}>
          {s.direction === "long" ? "achat" : "vente"}
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

      {perime && (
        <div className="warn">
          <strong>Niveaux non actualisés.</strong> Ils datent du dernier scan,
          pas de maintenant. À recalculer sur le prix courant avant tout ordre.
        </div>
      )}

      <div className="levels">
        <Level label="Entrée" value={fmtPrice(s.entry)} />
        <Level label="Stop" value={fmtPrice(s.stop)} />
        <Level label="Objectif" value={fmtPrice(s.target)} />
        <Level label="R/R" value={fmtNum(s.rr)} />
      </div>
      {/* Le plan à l'échelle des prix : deux bandes dont le rapport des
          largeurs *est* le R/R, plutôt qu'un chiffre à interpréter. */}
      <PlanTrade
        entry={s.entry}
        stop={s.stop}
        target={s.target}
        price={s.price}
        direction={s.direction === "short" ? "short" : "long"}
      />

      {s.stop_basis && (
        <div className="plan-basis">
          <div>
            <span className="muted">P(gain)</span>{" "}
            <span className="predit" title="Prédiction du modèle, pas une mesure">
              {(s.win_prob * 100).toFixed(0)} %
            </span>
            {" · "}
            <span className="muted">espérance</span>{" "}
            <span
              className={`predit ${s.expected_r > 0 ? "up" : "down"}`}
              title="Prédiction du modèle, pas une mesure"
            >
              {s.expected_r >= 0 ? "+" : ""}
              {fmtNum(s.expected_r, 3)} R
            </span>
          </div>
          <div className="muted">stop ← {s.stop_basis}</div>
          <div className="muted">objectif ← {s.target_basis}</div>
        </div>
      )}

      {/* Ce que la tranche de score a produit en rejeu. Deux chiffres mesurés
          en regard de deux chiffres prédits : c'est le seul moyen de savoir ce
          que vaut la prédiction affichée juste au-dessus. */}
      {bande && (
        <div className={`record ${bande.esperance > 0 ? "good" : "bad"}`}>
          <div className="record-head">Ce qu&rsquo;a donné cette tranche</div>
          Sur les <strong>{bande.trades} trades</strong> rejoués avec un score
          de {bande.tranche},{" "}
          <strong>{fmtNum(bande.win_rate, 1)} % de réussite</strong> et une
          espérance de{" "}
          <strong className={bande.esperance > 0 ? "up" : "down"}>
            {bande.esperance >= 0 ? "+" : ""}
            {fmtNum(bande.esperance, 3)} R
          </strong>
          .{" "}
          {bande.esperance <= 0 ? (
            <>
              La tranche est <strong>perdante</strong> sur l&rsquo;échantillon
              mesuré.
              {s.expected_r > 0 && (
                <>
                  {" "}
                  Le modèle annonce pourtant une espérance positive ci-dessus :
                  c&rsquo;est un désaccord, pas un détail.
                </>
              )}
            </>
          ) : (
            <>
              Le modèle annonçait {fmtNum(bande.win_rate - bande.ecart_a_la_prediction, 1)} %
              de réussite, soit {bande.ecart_a_la_prediction >= 0 ? "moins" : "plus"} que
              l&rsquo;observé.
            </>
          )}
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
