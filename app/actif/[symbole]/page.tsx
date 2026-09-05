/**
 * Page d'un actif.
 *
 * Le tableau de bord répond à « que faut-il faire aujourd'hui ». Cette page
 * répond à une autre question, qu'aucune autre ne traitait : « qu'est-ce que
 * ce moteur a raconté sur cet actif, et qu'est-ce que ça valait ». Elle
 * n'existe donc que grâce à la mémoire (`data/history.json`) et au suivi des
 * signaux émis (`data/suivi.json`) — sans eux, il n'y aurait qu'un prix.
 *
 * Trois strates, dans cet ordre : le prix, le score du moteur en dessous sur
 * le même axe temporel, puis les signaux qu'il a portés, épinglés à leur date.
 */
import { notFound } from "next/navigation";

import {
  fmtNum,
  fmtPrice,
  getHistory,
  getSnapshot,
  getSuivi,
  seuils,
  timeAgo,
  REGIME_LABELS,
  type Issue,
  type Signal,
} from "@/lib/data";
import { serieActif } from "@/lib/series";
import { Chart, CourbeActif, PlanTrade } from "@/components/charts";
import { Topbar } from "@/components/topbar";
import { Kpi } from "../../sections";

export const dynamic = "force-dynamic";

const ISSUES: Record<Issue, { libelle: string; classe: string }> = {
  cible: { libelle: "objectif atteint", classe: "cible" },
  stop: { libelle: "stop touché", classe: "stop" },
  expiration: { libelle: "expiré", classe: "neutre" },
  en_cours: { libelle: "en cours", classe: "encours" },
  hors_portee: { libelle: "hors portée", classe: "neutre" },
  indetermine: { libelle: "indéterminé", classe: "neutre" },
};

const dateLongue = (iso: string) =>
  new Date(iso).toLocaleString("fr-FR", {
    day: "2-digit",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });

export async function generateMetadata({
  params,
}: {
  params: Promise<{ symbole: string }>;
}) {
  const { symbole } = await params;
  const sym = decodeURIComponent(symbole).toUpperCase();
  const hist = await getHistory();
  const actif = hist?.actifs?.[sym];
  return {
    title: actif ? `${sym} — ${actif.label} · Jimbot` : `${sym} · Jimbot`,
    description: actif
      ? `Prix, score du moteur et signaux émis sur ${actif.label}.`
      : "Actif inconnu.",
  };
}

export default async function PageActif({
  params,
}: {
  params: Promise<{ symbole: string }>;
}) {
  const { symbole } = await params;
  const sym = decodeURIComponent(symbole).toUpperCase();

  const [hist, suivi, snap] = await Promise.all([
    getHistory(),
    getSuivi(),
    getSnapshot(),
  ]);

  const serie = serieActif(hist, sym, suivi?.signaux ?? []);
  if (!serie) notFound();

  const seuil = seuils(snap);
  const courant = snap?.signals.find((s) => s.symbol === sym);
  const signaux = (suivi?.signaux ?? []).filter((s) => s.symbol === sym);
  const tranches = signaux.filter((s) => s.r_multiple !== null);
  const totalR = tranches.reduce((a, s) => a + (s.r_multiple ?? 0), 0);
  const gagnants = signaux.filter((s) => s.issue === "cible").length;

  return (
    <>
      <Topbar
        stamp={
          hist?.generated_at
            ? `dernier scan ${timeAgo(hist.generated_at)}`
            : "historique"
        }
        generatedAt={hist?.generated_at ?? undefined}
        actif="tableau"
      />
      <main className="wrap" id="contenu">
        <p className="fil">
          <a href="/">Tableau de bord</a> · {serie.klass || "actif"}
        </p>

        <div className="actif-head">
          <span className="actif-sym">{serie.symbol}</span>
          <span className="actif-nom">{serie.label}</span>
          <span className="actif-prix">
            {fmtPrice(serie.prix.dernier)}
            <span
              className={`actif-var ${serie.prix.variation > 0 ? "up" : serie.prix.variation < 0 ? "down" : "muted"}`}
              style={{ marginLeft: 10 }}
            >
              {serie.prix.variation >= 0 ? "+" : ""}
              {fmtNum(serie.prix.variation)} %
            </span>
          </span>
        </div>

        {!serie.suivi && (
          <div className="warn" style={{ maxWidth: "76ch" }}>
            <strong>Actif retiré de l’univers.</strong> Son dernier relevé date
            du {dateLongue(serie.fin)} et plus rien ne l’alimente : ce qui suit
            est un archivage, pas une observation en cours.
          </div>
        )}

        <div className="kpis" style={{ marginTop: 20 }}>
          <Kpi
            label="Score du moteur"
            value={`${serie.score.dernier > 0 ? "+" : ""}${serie.score.dernier.toFixed(0)}`}
            tone={serie.score.dernier > 0 ? "up" : serie.score.dernier < 0 ? "down" : undefined}
            spark={serie.points.map((p) => p.score)}
          />
          <Kpi
            label="Régime"
            value={
              REGIME_LABELS[serie.points[serie.points.length - 1].regime] ??
              serie.points[serie.points.length - 1].regime ??
              "—"
            }
          />
          <Kpi label="Relevés" value={String(serie.points.length)} />
          <Kpi label="Signaux émis" value={String(signaux.length)} />
          {/* Un décompte, pas un pourcentage. Sur un ou deux signaux, « 100 % de
              réussite » est vrai et parfaitement trompeur : la fraction dit la
              même chose sans suggérer une fréquence qu'on n'a pas mesurée. */}
          <Kpi
            label="Objectifs atteints"
            value={tranches.length ? `${gagnants} / ${tranches.length}` : "—"}
          />
          <Kpi
            label="Total"
            value={tranches.length ? `${totalR >= 0 ? "+" : ""}${fmtNum(totalR, 2)} R` : "—"}
            tone={tranches.length ? (totalR > 0 ? "up" : "down") : undefined}
          />
        </div>

        {tranches.length > 0 && tranches.length < 30 && (
          <p className="note" style={{ maxWidth: "76ch" }}>
            {tranches.length === 1
              ? "Un seul signal tranché sur cet actif"
              : `${tranches.length} signaux tranchés sur cet actif`}{" "}
            — de quoi raconter ce qui s’est passé, pas de quoi en tirer un taux
            de réussite. Le bilan d’ensemble, et ce qu’il vaut, sont sur le{" "}
            <a href="/">tableau de bord</a>.
          </p>
        )}

        <div className="charts" style={{ marginTop: 20 }}>
          <Chart
            wide
            title={`${serie.symbol} — prix et score`}
            sub={`${serie.points.length} relevés du ${dateLongue(serie.debut)} au ${dateLongue(serie.fin)}`}
            foot={
              <>
                Un point par scan. L’axe est le temps, pas le rang du scan&nbsp;:
                un <span style={{ opacity: 0.7 }}>trait pointillé pâle</span>{" "}
                relie deux relevés séparés par plus de trois heures — l’ordonnanceur
                de GitHub abandonne des exécutions, et le prix entre ces deux
                mesures n’a pas été observé. Les triangles marquent les signaux
                émis, à leur date et à leur prix d’entrée&nbsp;; leur couleur dit
                ce qu’ils sont devenus. Le panneau du bas porte le score signé,
                avec le seuil de déclenchement à ±{seuil.signal}.
              </>
            }
          >
            <CourbeActif serie={serie} seuil={seuil.signal} />
          </Chart>
        </div>

        {courant && <Position courant={courant} seuilSignal={seuil.signal} />}

        <section>
          <h2>Signaux émis sur {serie.symbol}</h2>
          {signaux.length ? (
            <>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Émis le</th>
                      <th>Sens</th>
                      <th className="num">Score</th>
                      <th className="num">Entrée</th>
                      <th className="num">Stop</th>
                      <th className="num">Objectif</th>
                      <th>Régime</th>
                      <th>Issue</th>
                      <th className="num">Résultat</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signaux.map((s) => {
                      const issue = ISSUES[s.issue] ?? ISSUES.indetermine;
                      return (
                        <tr key={s.id}>
                          <td className="muted">
                            {dateLongue(s.premiere_emission)}
                            {s.emissions > 1 && (
                              <span className="pill" style={{ marginLeft: 6 }}>
                                ×{s.emissions}
                              </span>
                            )}
                          </td>
                          <td className={s.direction === "long" ? "up" : "down"}>
                            {s.direction === "long" ? "achat" : "vente"}
                          </td>
                          <td className="num">{s.score.toFixed(0)}</td>
                          <td className="num">{fmtPrice(s.entry)}</td>
                          <td className="num">{fmtPrice(s.stop)}</td>
                          <td className="num">{fmtPrice(s.target)}</td>
                          <td className="muted">
                            {REGIME_LABELS[s.regime] ?? s.regime}
                          </td>
                          <td>
                            <span className={`issue ${issue.classe}`}>
                              {issue.libelle}
                            </span>
                          </td>
                          <td
                            className={`num ${
                              s.r_multiple === null
                                ? "muted"
                                : s.r_multiple > 0
                                  ? "up"
                                  : "down"
                            }`}
                          >
                            {s.r_multiple !== null ? (
                              <>
                                {s.r_multiple >= 0 ? "+" : ""}
                                {fmtNum(s.r_multiple, 2)} R
                              </>
                            ) : s.r_courant !== null ? (
                              <span title="Le trade court encore : valeur au dernier prix connu">
                                ({s.r_courant >= 0 ? "+" : ""}
                                {fmtNum(s.r_courant, 2)} R)
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
                Les émissions rapprochées d’un même signal sont regroupées&nbsp;:
                «&nbsp;×3&nbsp;» signifie que le moteur a réémis la même
                configuration à trois scans consécutifs. Le plan affiché est
                celui de la première émission. Le détail de la méthode est sur le{" "}
                <a href="/#contenu">tableau de bord</a>.
              </p>
            </>
          ) : (
            <div className="empty">
              Aucun signal émis sur {serie.symbol} depuis l’ouverture du suivi.
              Le moteur l’a analysé à chaque scan sans jamais franchir le seuil
              de ±{seuil.signal} — c’est le cas le plus fréquent, et ce n’en est
              pas moins une décision.
            </div>
          )}
        </section>

        <footer>
          Historique reconstitué depuis les instantanés committés par le moteur.
          Il est borné à {hist?.points_max ?? 0} relevés par actif&nbsp;: au-delà,
          les plus anciens sont écartés. Le suivi des signaux, lui, ne l’est pas
          — une issue établie est figée définitivement.
          <br />
          Analyse automatisée à but informatif. Ne constitue pas un conseil en
          investissement.
        </footer>
      </main>
    </>
  );
}

/**
 * Ce que le moteur dit de l'actif *maintenant*.
 *
 * Placé après l'historique, et non avant : la page raconte d'abord ce qui
 * s'est passé, ensuite seulement ce qui est proposé. L'ordre inverse ferait
 * de l'historique une justification de la position du jour.
 */
function Position({
  courant,
  seuilSignal,
}: {
  courant: Signal;
  seuilSignal: number;
}) {
  const oriente = courant.bias !== "neutre";
  return (
    <section>
      <h2>Lecture du moteur au dernier scan</h2>
      <div className="levels" style={{ maxWidth: 620 }}>
        <div className="level">
          <div className="level-label">Score</div>
          <div className="level-value">{courant.score.toFixed(0)} / 100</div>
        </div>
        <div className="level">
          <div className="level-label">Régime</div>
          <div className="level-value" style={{ fontSize: "var(--t-sm)" }}>
            {REGIME_LABELS[courant.regime.name] ?? courant.regime.name}
          </div>
        </div>
        <div className="level">
          <div className="level-label">Volatilité</div>
          <div className="level-value">{fmtNum(courant.atr_pct)} %</div>
        </div>
        <div className="level">
          <div className="level-label">Biais</div>
          <div className="level-value" style={{ fontSize: "var(--t-sm)" }}>
            {courant.bias === "long"
              ? "achat"
              : courant.bias === "short"
                ? "vente"
                : "aucun"}
          </div>
        </div>
      </div>

      {courant.actionable ? (
        <>
          <p className="note" style={{ maxWidth: "76ch" }}>
            Le seuil de ±{seuilSignal} est franchi&nbsp;: un signal est en cours
            d’émission sur cet actif. Le plan ci-dessous est celui du dernier
            scan et doit être recalculé sur le prix courant avant tout ordre.
          </p>
          <div style={{ maxWidth: 620, marginTop: 12 }}>
            <PlanTrade
              entry={courant.entry}
              stop={courant.stop}
              target={courant.target}
              price={courant.price}
              direction={courant.direction === "short" ? "short" : "long"}
            />
          </div>
        </>
      ) : (
        <p className="note" style={{ maxWidth: "76ch" }}>
          Aucun signal&nbsp;: le score n’atteint pas ±{seuilSignal}
          {oriente
            ? `, malgré une orientation ${courant.bias === "long" ? "à l’achat" : "à la vente"}`
            : " et le moteur ne lui trouve pas d’orientation"}
          . Le plan n’est pas affiché parce qu’il n’y en a pas — en montrer un
          laisserait croire à une configuration qui n’a pas été retenue.
        </p>
      )}
    </section>
  );
}
