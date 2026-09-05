/**
 * Page « Courbes ».
 *
 * Le tableau de bord répond à « qu'est-ce que le moteur dit aujourd'hui ».
 * Cette page répond à « est-ce que le moteur a raison », et c'est une autre
 * question : elle demande des séries, pas des états. Rien n'y est calculé —
 * tout vient de `lib/series.ts`, le même module que la page d'accueil et que
 * l'API, pour qu'une courbe ne puisse jamais contredire un tableau.
 */
import {
  fmtNum,
  getBacktest,
  getProbe,
  getSnapshot,
  getTrades,
  timeAgo,
} from "@/lib/data";
import {
  calibration,
  courbesIC,
  cumulBacktest,
  cumulTradesPapier,
  distributionR,
  excursions,
  serieEquite,
  universSigne,
} from "@/lib/series";
import {
  BarresCategories,
  BarresUnivers,
  Calibration,
  Chart,
  CourbeCapital,
  CourbeCumulR,
  Distribution,
  Legend,
  NuageExcursions,
  PetitesMultiplesIC,
} from "@/components/charts";
import { Topbar } from "@/components/topbar";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Jimbot — courbes",
  description:
    "Capital, R cumulés, calibration, distribution des résultats et pouvoir prédictif des facteurs.",
};

export default async function Courbes() {
  const [snap, trades, bt, probe] = await Promise.all([
    getSnapshot(),
    getTrades(),
    getBacktest(),
    getProbe(),
  ]);

  if (!snap) {
    return (
      <>
        <Topbar stamp="en attente du premier scan" actif="courbes" />
        <main className="wrap" id="contenu">
          <section>
            <div className="empty">
              Aucune donnée pour l’instant. Les courbes apparaîtront après le
              premier scan.
            </div>
          </section>
        </main>
      </>
    );
  }

  const capital = serieEquite(snap.portfolio);
  const papier = cumulTradesPapier(trades);
  const backtest = cumulBacktest(bt);
  const rsBacktest = (bt?.trades ?? []).map((t) => t.r_multiple);
  const cal = calibration(bt);
  const seuil = bt?.effet_de_la_structure?.adosse_a_la_structure?.seuil_rentabilite;
  const ic = courbesIC(probe);
  const nuage = excursions(bt);

  const categories = (
    src?: Record<string, { trades: number; esperance: number; win_rate: number }>,
  ) =>
    Object.entries(src ?? {})
      .map(([label, v]) => ({
        label: label.replace(/_/g, " "),
        valeur: v.esperance,
        n: v.trades,
      }))
      .sort((a, b) => b.valeur - a.valeur);

  const parRegime = categories(bt?.calibration?.par_regime);
  const parClasse = categories(bt?.calibration?.par_classe);

  return (
    <>
      <Topbar
        stamp={`dernier scan ${timeAgo(snap.generated_at)}`}
        generatedAt={snap.generated_at}
        actif="courbes"
      />

      <main className="wrap" id="contenu">
        <div className="masthead">
          <h1>Par où le capital est passé</h1>
          <p>
            Les mêmes nombres que le tableau de bord, mis en forme. Un chiffre
            de performance dit où l’on est arrivé ; une courbe dit par où l’on
            est passé, et c’est la seule chose qui permette de distinguer{" "}
            <strong>un avantage d’une série de coups heureux</strong>.
          </p>
        </div>

        {/* ---------------- Portefeuille papier ---------------- */}
        <section id="capital">
          <h2>Portefeuille papier</h2>
          <div className="charts">
            <Chart
              wide
              title="Capital et repli"
              sub={`${capital.points.length} relevés · ${fmtNum(capital.final, 0)}`}
              foot={
                <>
                  Un point par scan, du premier au dernier. Le trait pointillé
                  marque le capital de départ&nbsp;: tout ce qui est au-dessus
                  est acquis, tout ce qui est en dessous reste à rattraper. Le
                  panneau du bas mesure la distance au plus haut atteint —{" "}
                  {capital.repli_max < 0
                    ? `le pire creux a coûté ${fmtNum(Math.abs(capital.repli_max), 0)} depuis un sommet.`
                    : "aucun repli enregistré à ce jour."}
                  <Legend
                    items={[
                      { label: "capital", tone: capital.final >= capital.initial ? "up" : "down" },
                      { label: "capital de départ", tone: "ghost" },
                      { label: "repli depuis le sommet", tone: "down", block: true },
                    ]}
                  />
                </>
              }
            >
              <CourbeCapital serie={capital} />
            </Chart>

            <Chart
              title="R cumulés — trades réels"
              sub={`${papier.gagnants} gagnants · ${papier.perdants} perdants`}
              foot={
                trades.length
                  ? `Somme des R des trades effectivement fermés par le portefeuille papier. ${
                      trades.length < 30
                        ? "L’échantillon est trop court pour conclure quoi que ce soit : une courbe de moins de trente trades décrit surtout du hasard."
                        : "L’échantillon commence à être lisible."
                    }`
                  : "Aucun trade fermé pour l’instant."
              }
            >
              {trades.length ? (
                <CourbeCumulR serie={papier} h={200} seed="papier" />
              ) : (
                <div className="empty">Aucun trade fermé.</div>
              )}
            </Chart>

            {backtest && (
              <Chart
                title="R cumulés — rejeu historique"
                sub={`${backtest.gagnants + backtest.perdants} trades · repli ${fmtNum(backtest.repli_max, 1)} R`}
                foot={
                  <>
                    Le même calcul sur la fenêtre rejouée, où l’échantillon est
                    assez large pour dire quelque chose. La pente compte moins
                    que la profondeur des creux&nbsp;: un système qui finit à{" "}
                    {fmtNum(backtest.total, 1)} R après être descendu à{" "}
                    {fmtNum(backtest.repli_max, 1)} R exige de tenir pendant la
                    descente.
                  </>
                }
              >
                <CourbeCumulR serie={backtest} h={200} seed="bt" />
              </Chart>
            )}
          </div>
        </section>

        {/* ---------------- Anatomie des résultats ---------------- */}
        {rsBacktest.length > 0 && (
          <section id="resultats">
            <h2>Anatomie des résultats</h2>
            <p className="lede">
              Une espérance positive peut recouvrir deux systèmes très
              différents&nbsp;: beaucoup de petits gains, ou peu de gains
              lourds. Ces deux graphiques disent lequel.
            </p>
            <div className="charts">
              <Chart
                title="Distribution des résultats"
                sub={`${rsBacktest.length} trades rejoués`}
                foot={
                  <>
                    Deux amas et presque rien entre les deux&nbsp;: c’est la
                    signature d’un plan à stop et objectif fixes. Le moteur ne
                    sort pas au jugé, il attend l’un ou l’autre. La question
                    n’est donc pas la forme de la distribution mais le rapport
                    des effectifs entre les deux amas.
                  </>
                }
              >
                <Distribution paniers={distributionR(rsBacktest)} h={200} />
              </Chart>

              <Chart
                title="Excursions favorables et défavorables"
                sub={`${nuage.length} trades`}
                foot={
                  <>
                    Chaque point est un trade&nbsp;: à gauche, le plus bas
                    traversé avant la sortie&nbsp;; en hauteur, le plus haut
                    atteint. Un perdant situé au-dessus du trait horizontal est
                    allé chercher son objectif sans qu’on soit resté dedans —
                    ce sont ces points-là qui condamnent un stop, pas le taux de
                    réussite global.
                    <Legend
                      items={[
                        { label: "objectif atteint", tone: "up", block: true },
                        { label: "stop ou expiration", tone: "down", block: true },
                      ]}
                    />
                  </>
                }
              >
                <NuageExcursions points={nuage} h={280} />
              </Chart>
            </div>
          </section>
        )}

        {/* ---------------- Calibration ---------------- */}
        {cal.length > 0 && (
          <section id="calibration">
            <h2>Le modèle a-t-il raison sur lui-même</h2>
            <div className="charts">
              <Chart
                title="Réussite prédite contre réussite observée"
                sub={
                  typeof bt?.calibration?.correlation_score_esperance === "number"
                    ? `corrélation score / espérance ${fmtNum(bt.calibration.correlation_score_esperance, 3)}`
                    : undefined
                }
                foot={
                  <>
                    Deux propriétés distinctes, visibles ici seulement
                    ensemble. La <strong>calibration</strong> est l’alignement
                    des deux barres&nbsp;: le modèle sait-il annoncer sa propre
                    probabilité de gain. La <strong>discrimination</strong> est
                    la pente d’une tranche à l’autre&nbsp;: une conviction plus
                    forte donne-t-elle un meilleur résultat. Un modèle peut
                    être calibré sans discriminer, et c’est alors un seuil
                    inutile.
                    <Legend
                      items={[
                        { label: "prédite par le modèle", tone: "ghost", block: true },
                        { label: "observée", tone: "up", block: true },
                      ]}
                    />
                  </>
                }
              >
                <Calibration points={cal} seuil={seuil} h={220} />
              </Chart>

              {parRegime.length > 0 && (
                <Chart
                  title="Espérance par régime et par classe"
                  sub="en R par trade"
                  foot={
                    <>
                      Découpage a posteriori sur un échantillon déjà réduit :
                      chaque barre repose sur quelques dizaines de trades. Ces
                      écarts indiquent où regarder, ils ne démontrent rien.
                    </>
                  }
                >
                  <BarresCategories items={[...parRegime, ...parClasse]} />
                </Chart>
              )}
            </div>
          </section>
        )}

        {/* ---------------- Facteurs ---------------- */}
        {ic.length > 0 && (
          <section id="facteurs">
            <h2>Pouvoir prédictif des facteurs</h2>
            <p className="lede">
              Un cadre par facteur, tous à la même échelle — c’est le point
              essentiel&nbsp;: recadrer chaque facteur sur ses propres bornes
              ferait paraître le bruit aussi ample que le signal. L’axe
              horizontal est l’horizon de prévision en bougies, l’axe vertical
              la corrélation de rang avec le rendement réalisé ensuite. Une
              étoile marque ce qui se distingue du bruit.
            </p>
            <PetitesMultiplesIC courbes={ic} />
            <p className="note">
              {probe?.coefficients?.observations.toLocaleString("fr-FR")}{" "}
              observations. Les trois facteurs de suivi de tendance ressortent
              <strong> négatifs</strong>&nbsp;: une lecture haussière du prix
              est suivie, en moyenne, d’un rendement négatif. Les pondérations
              du moteur ont été réécrites à partir de ces mesures plutôt que
              supposées.
            </p>
          </section>
        )}

        {/* ---------------- Univers ---------------- */}
        <section id="univers">
          <h2>Le marché aujourd’hui</h2>
          <div className="charts">
            <Chart
              wide
              title="Score signé de chaque actif suivi"
              sub={`${snap.counts.analysed} actifs · seuil ±58`}
              foot={
                <>
                  Positif à l’achat, négatif à la vente. Les barres pâles
                  n’atteignent pas le seuil de déclenchement et ne donnent lieu
                  à aucune position&nbsp;; les barres pleines le franchissent.
                  Une journée sans barre pleine est une journée sans signal, et
                  c’est le cas le plus fréquent.
                </>
              }
            >
              <BarresUnivers items={universSigne(snap)} w={860} />
            </Chart>
          </div>
        </section>

        {/* ---------------- Le service ---------------- */}
        <section id="service">
          <h2>Service de courbes</h2>
          <p style={{ maxWidth: "68ch", marginBottom: 16 }}>
            Toutes ces séries sont exposées, dérivées et prêtes à tracer, par{" "}
            <a href="/api/curves" target="_blank" rel="noopener noreferrer">
              <code>/api/curves</code>
            </a>
            . Le même point d’entrée sait aussi rendre le graphique lui-même en
            SVG&nbsp;: l’image est autonome — elle embarque ses couleurs et
            suit la préférence sombre du lecteur — donc utilisable dans un{" "}
            <code>README</code>, un message ou une page tierce, sans feuille de
            style ni JavaScript.
          </p>

          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Route</th>
                  <th>Contenu</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ["/api/curves", "Index et toutes les séries en une réponse"],
                    ["/api/curves?serie=capital", "Capital du portefeuille papier et repli"],
                    ["/api/curves?serie=cumul_backtest", "R cumulés sur la fenêtre rejouée"],
                    ["/api/curves?serie=distribution", "Effectifs par tranche de R"],
                    ["/api/curves?serie=calibration", "Réussite prédite contre observée"],
                    ["/api/curves?serie=excursions", "MFE et MAE de chaque trade"],
                    ["/api/curves?serie=ic", "Coefficients d’information par facteur"],
                    ["/api/curves?serie=univers", "Score signé des actifs suivis"],
                    [
                      "/api/curves?serie=capital&format=svg&w=860&h=240",
                      "Le graphique lui-même, en SVG autonome",
                    ],
                  ] as [string, string][]
                ).map(([route, desc]) => (
                  <tr key={route}>
                    <td>
                      <a href={route} target="_blank" rel="noopener noreferrer">
                        <code>{route}</code>
                      </a>
                    </td>
                    <td className="muted wide">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="note">
            Les séries sont dérivées par le même module que cette page. Une
            courbe servie par l’API ne peut donc pas diverger de celle qui est
            affichée ici — c’est la raison d’être de ce découpage, plus que la
            commodité.
          </p>
        </section>

        <footer>
          Jimbot — analyse automatisée à but informatif. Ne constitue pas un
          conseil en investissement. Le portefeuille est simulé&nbsp;: aucun
          ordre réel n’est transmis. Les performances passées, réelles ou
          simulées, ne préjugent pas des performances futures.
        </footer>
      </main>
    </>
  );
}
