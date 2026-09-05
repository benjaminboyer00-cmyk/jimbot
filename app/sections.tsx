/**
 * Sections du tableau de bord.
 *
 * Séparées de `page.tsx` pour que la page reste lisible : elle décrit l'ordre
 * de lecture, chaque section décrit son propre contenu.
 */
import {
  fmtCompact,
  fmtNum,
  fmtPrice,
  REGIME_LABELS,
  type Backtest,
  type Issue,
  type Probe,
  type Report,
  type RiskOff,
  type Signal,
  type Speech,
  type Suivi,
  type Trade,
} from "@/lib/data";

// Les formes `Backtest` et `Probe` décrivent des fichiers du moteur : elles
// vivent dans `lib/data.ts`. On les ré-expose ici pour ne pas casser les
// imports existants.
export type { Backtest, Probe };

import { calibration, courbesIC, cumulSuivi, discrimination } from "@/lib/series";
import {
  Calibration,
  Chart,
  CourbeCumulR,
  PetitesMultiplesIC,
  Sparkline,
} from "@/components/charts";

/* ------------------------------------------------------------------ */
/* Chiffre clé                                                         */
/*                                                                     */
/* Vivait dans `page.tsx`, où il n'était pas exportable sans créer un   */
/* cycle d'imports. Il est ici parce que plusieurs sections l'utilisent.*/
/* ------------------------------------------------------------------ */

export function Kpi({
  label,
  value,
  tone,
  spark,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  /** Série d'accompagnement : la forme du chemin parcouru sous le chiffre. */
  spark?: number[];
}) {
  return (
    <div className="kpi">
      <div className={`kpi-value ${tone ?? ""}`}>{value}</div>
      <div className="kpi-label">{label}</div>
      {spark && spark.length > 1 && (
        <div className="kpi-spark">
          <Sparkline values={spark} tone={tone} seed={label} h={22} />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Résumé d'actualité                                                  */
/* ------------------------------------------------------------------ */
export function NewsSummary({
  summary,
  engine,
  speeches,
}: {
  summary: string;
  engine?: string;
  speeches: Speech[];
}) {
  if (!summary) return null;
  return (
    <section>
      <h2>Résumé de l’actualité</h2>
      <div className="prose">
        {summary.split("\n\n").map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>

      {speeches.length > 0 && (
        <>
          <h3 style={{ marginTop: 22 }}>Discours de politique monétaire</h3>
          <div className="cards">
            {speeches.slice(0, 3).map((s, i) => {
              const accommodant = s.tone > 0;
              const effetOr = s.impact?.XAUUSD ?? 0;
              return (
                <article className="card" key={i}>
                  <div className="card-head">
                    <div>
                      <div className="card-sym">{s.speaker.toUpperCase()}</div>
                      <div className="card-name">importance {s.importance.toFixed(2)}/1.00</div>
                    </div>
                    <span className={`badge ${accommodant ? "long" : "short"}`}>
                      {accommodant ? "ACCOMMODANT" : "RESTRICTIF"}
                    </span>
                  </div>
                  <p style={{ fontSize: 13, marginTop: 10 }}>
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noopener noreferrer">
                        {s.title}
                      </a>
                    ) : (
                      s.title
                    )}
                  </p>
                  <div className="plan-basis">
                    <div>
                      <span className="muted">tonalité</span>{" "}
                      <span className={accommodant ? "up" : "down"}>
                        {s.tone > 0 ? "+" : ""}
                        {fmtNum(s.tone, 1)}
                      </span>
                      {" · "}
                      <span className="muted">effet attendu sur l’or</span>{" "}
                      <span className={effetOr > 0 ? "up" : "down"}>
                        {effetOr > 0 ? "+" : ""}
                        {fmtNum(effetOr)}
                      </span>
                    </div>
                    <div className="muted">
                      {s.terms.map((t) => (
                        <span key={t} className="pill" style={{ marginRight: 4 }}>
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          <p className="note">
            L’or ne réagit pas aux bénéfices d’entreprise mais aux taux réels :
            une tonalité accommodante lui est favorable, une tonalité
            restrictive lui est défavorable. L’effet affiché est calculé à
            partir de la tonalité mesurée et de la sensibilité de l’actif aux
            taux — il n’est jamais rédigé.
          </p>
        </>
      )}
      {engine && <p className="note">Rédaction : {engine}.</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Suggestions du moment                                               */
/* ------------------------------------------------------------------ */
/**
 * Motif pour lequel une orientation n'est pas retenue.
 *
 * Trois cas bien distincts, qu'il serait trompeur de confondre :
 *
 * - sous un score de 50, le modèle attribue un avantage **directionnel**
 *   strictement nul. Une espérance malgré tout positive ne vient donc pas
 *   d'une conviction sur le sens du marché, mais du seul placement du stop
 *   derrière un niveau solide — c'est réel, mais bien plus faible ;
 * - une espérance négative signale que la structure bloque le chemin vers
 *   l'objectif ;
 * - au-delà de 50 avec une espérance positive, il ne manque que la
 *   conviction pour franchir le seuil.
 */
function verdict(s: Signal): { text: string; tone: "neutral" | "bad" } {
  if (s.expected_r <= 0)
    return { text: "structure défavorable — objectif hors d'atteinte", tone: "bad" };
  if (s.score < 50)
    return {
      text: "aucune conviction directionnelle — l'espérance ne vient que du stop",
      tone: "neutral",
    };
  return {
    text: `espérance positive, conviction insuffisante (${s.score.toFixed(0)}/58)`,
    tone: "neutral",
  };
}

export function Watchlist({ items }: { items: Signal[] }) {
  if (!items.length) return null;
  return (
    <section>
      <h2>Les moins défavorables du moment</h2>
      <p className="note" style={{ marginTop: 0, marginBottom: 14 }}>
        Classées de la moins défavorable à la plus défavorable. Aucune n’atteint
        le seuil de déclenchement et aucune ne donne lieu à une position :
        ce ne sont pas des opportunités, seulement ce qui s’en approche le plus
        aujourd’hui.
        <br />
        <strong>Une espérance positive ici ne signifie pas un bon trade.</strong>{" "}
        Sous un score de 50, le modèle n’accorde aucun avantage directionnel :
        l’espérance affichée provient alors uniquement du placement du stop
        derrière un niveau solide. C’est un avantage réel mais faible, et il ne
        dit rien du sens dans lequel le marché va partir.
      </p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Symbole</th>
              <th>Biais</th>
              <th className="num">Score</th>
              <th className="num">Entrée</th>
              <th className="num">Stop</th>
              <th className="num">Objectif</th>
              <th className="num">R/R</th>
              <th className="num">
                <span className="predit" title="Prédiction du modèle">P(gain)</span>
              </th>
              <th className="num">
                <span className="predit" title="Prédiction du modèle">Espérance</span>
              </th>
              <th className="wide">Lecture</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.symbol}>
                <td style={{ fontWeight: 600 }}>{s.symbol}</td>
                <td className={s.bias === "long" ? "up" : "down"}>
                  {s.bias === "long" ? "achat" : "vente"}
                </td>
                <td className="num">{s.score.toFixed(0)}</td>
                <td className="num">{fmtPrice(s.entry)}</td>
                <td className="num">{fmtPrice(s.stop)}</td>
                <td className="num">{fmtPrice(s.target)}</td>
                <td className="num">{fmtNum(s.rr)}</td>
                <td className="num muted">{(s.win_prob * 100).toFixed(0)} %</td>
                <td className={`num ${s.expected_r > 0 ? "up" : "down"}`}>
                  {s.expected_r >= 0 ? "+" : ""}
                  {fmtNum(s.expected_r, 3)} R
                </td>
                <td className={verdict(s).tone === "bad" ? "down" : "muted"}>
                  {verdict(s).text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Journal des trades                                                  */
/* ------------------------------------------------------------------ */
export function TradeJournal({ trades }: { trades: Trade[] }) {
  if (!trades.length) {
    return (
      <div className="empty" style={{ marginTop: 20 }}>
        Aucun trade fermé. Les statistiques apparaîtront dès qu’un historique
        existera — un taux de réussite sur trois trades n’a aucune valeur.
      </div>
    );
  }

  const gagnants = trades.filter((t) => t.pnl > 0).length;
  const meilleur = Math.max(...trades.map((t) => t.r_multiple));
  const pire = Math.min(...trades.map((t) => t.r_multiple));

  return (
    <>
      <h3 style={{ marginTop: 24 }}>
        Journal des trades{" "}
        <span className="muted" style={{ fontWeight: 400 }}>
          — {trades.length} fermé(s), {gagnants} gagnant(s)
        </span>
      </h3>

      <div className="journal">
        {trades.slice(0, 25).map((t, i) => {
          const gagne = t.pnl > 0;
          return (
            <div className={`entry ${gagne ? "win" : "loss"}`} key={`${t.symbol}-${t.closed_at}-${i}`}>
              <div className="entry-r">
                {t.r_multiple >= 0 ? "+" : ""}
                {fmtNum(t.r_multiple)} R
              </div>
              <div className="entry-body">
                <div className="entry-head">
                  <strong>{t.symbol}</strong>
                  <span className={t.direction === "long" ? "up" : "down"}>
                    {t.direction === "long" ? "achat" : "vente"}
                  </span>
                  <span className="pill">{t.reason}</span>
                  <span className="muted">{REGIME_LABELS[t.regime] ?? t.regime}</span>
                </div>
                <div className="entry-detail muted">
                  entrée {fmtPrice(t.entry)} → sortie {fmtPrice(t.exit)} ·{" "}
                  <span className={gagne ? "up" : "down"}>
                    {t.pnl >= 0 ? "+" : ""}
                    {fmtNum(t.pnl)} ({t.pnl_pct >= 0 ? "+" : ""}
                    {fmtNum(t.pnl_pct)} %)
                  </span>{" "}
                  · clôturé le {t.closed_at.slice(0, 10)} à {t.closed_at.slice(11, 16)}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="note">
        Meilleur trade {meilleur >= 0 ? "+" : ""}
        {fmtNum(meilleur)} R, pire {fmtNum(pire)} R. Frais et glissement sont
        appliqués à l’entrée comme à la sortie. Lorsqu’une même bougie touche le
        stop et l’objectif, le stop est retenu : sans données infra-bougie,
        l’hypothèse favorable relèverait de l’auto-illusion.
      </p>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Rapports PDF                                                        */
/* ------------------------------------------------------------------ */
export function Reports({ reports }: { reports: Report[] }) {
  return (
    <section>
      <h2>Rapports PDF</h2>
      {reports.length ? (
        <div className="reports">
          {reports.slice(0, 12).map((r) => (
            <a
              className="report"
              key={r.name}
              href={`/api/reports?file=${encodeURIComponent(r.name)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span className="report-date">{r.date}</span>
              <span className="report-meta muted">{r.size_kb} Ko · PDF</span>
            </a>
          ))}
        </div>
      ) : (
        <div className="empty">
          Aucun rapport pour l’instant. Le premier est généré par le workflow
          quotidien.
        </div>
      )}
      <p className="note">
        Analyse complète : briefing, contexte mondial, configurations avec
        décomposition du score, graphiques de prix et de facteurs, portefeuille,
        actualités et méthode.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* API et MetaTrader                                                   */
/* ------------------------------------------------------------------ */
export function ApiSection() {
  const routes: [string, string][] = [
    ["/api/mt", "Flux au format MetaTrader — symboles, SL, TP, risque suggéré"],
    ["/api/mt?mode=all", "Signaux déclenchables et liste de surveillance"],
    ["/api/mt?symbol=GOLD", "Filtre sur un instrument (nom interne ou alias courtier)"],
    ["/api/signals", "État complet du scan, en JSON"],
    ["/api/reports", "Index des rapports PDF"],
  ];
  return (
    <section>
      <h2>API &amp; MetaTrader</h2>
      <p style={{ maxWidth: "76ch", marginBottom: 14 }}>
        Les signaux sont exposés en JSON, sans authentification et avec CORS
        ouvert : l’API est en lecture seule et ne sert que des données déjà
        publiques. Un Expert Advisor MetaTrader 5 prêt à l’emploi est fourni
        dans le dépôt, sous <code>metatrader/JimbotConnector.mq5</code>.
      </p>

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Route</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {routes.map(([route, desc]) => (
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

      <div className="warn" style={{ maxWidth: "76ch" }}>
        L’Expert Advisor est en <strong>lecture seule par défaut</strong> : il
        affiche et journalise, sans transmettre le moindre ordre. L’exécution
        automatique doit être activée explicitement et reste bornée par un
        risque maximal par position, un risque cumulé et un nombre de positions.
        Le volume est toujours déduit de la distance au stop, jamais d’un
        nombre de lots fixe.
      </div>

      <p className="note">
        Dans MetaTrader : Outils → Options → Expert Advisors → cocher
        «&nbsp;Autoriser les WebRequest&nbsp;» et ajouter le domaine de ce site.
        Les noms d’instruments varient d’un courtier à l’autre (le S&amp;P 500
        est «&nbsp;US500&nbsp;» chez l’un, «&nbsp;SPX500&nbsp;» chez l’autre) :
        l’API renvoie plusieurs alias et l’EA retient celui que votre courtier
        reconnaît.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Memecoins                                                           */
/* ------------------------------------------------------------------ */
export function Memecoins({
  items,
  report,
}: {
  items: {
    symbol: string;
    chain: string;
    price_usd: number;
    liquidity_usd: number;
    volume_24h: number;
    change_24h: number;
    age_hours: number;
    health_score: number;
    url: string;
  }[];
  report: { screened?: number; retained?: number };
}) {
  return (
    <section>
      <h2>
        Memecoins{" "}
        <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>
          — {report?.retained ?? items.length} retenu(s) sur {report?.screened ?? 0} criblé(s)
        </span>
      </h2>
      {items.length ? (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Jeton</th>
                <th>Chaîne</th>
                <th className="num">Prix</th>
                <th className="num">Liquidité</th>
                <th className="num">Volume 24h</th>
                <th className="num">24h</th>
                <th className="num">Âge</th>
                <th className="num">Robustesse</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={`${m.chain}-${m.symbol}`}>
                  <td style={{ fontWeight: 600 }}>
                    {m.url ? (
                      <a href={m.url} target="_blank" rel="noopener noreferrer">
                        {m.symbol}
                      </a>
                    ) : (
                      m.symbol
                    )}
                  </td>
                  <td className="muted">{m.chain}</td>
                  <td className="num">{fmtPrice(m.price_usd)}</td>
                  <td className="num">{fmtCompact(m.liquidity_usd)} $</td>
                  <td className="num">{fmtCompact(m.volume_24h)} $</td>
                  <td className={`num ${m.change_24h >= 0 ? "up" : "down"}`}>
                    {m.change_24h >= 0 ? "+" : ""}
                    {fmtNum(m.change_24h, 1)} %
                  </td>
                  <td className="num muted">{(m.age_hours / 24).toFixed(0)} j</td>
                  <td className="num">{fmtNum(m.health_score, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          Aucun jeton ne passe le filtre de survie sur ce cycle. C’est le cas
          courant : la liquidité exigée croît à mesure que le pool est jeune.
        </div>
      )}
      <p className="note">
        La robustesse mesure la profondeur de liquidité, l’activité et la
        maturité du pool — donc la capacité à sortir d’une position. Elle ne
        prédit aucune performance.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Contexte mondial                                                    */
/* ------------------------------------------------------------------ */
export function WorldContext({ riskOff }: { riskOff: RiskOff }) {
  const niveau = riskOff.level;
  const titre =
    niveau > 0.25 ? "Tension en hausse" : niveau < -0.25 ? "Détente" : "Climat neutre";
  const lecture =
    niveau > 0.25
      ? "Rotation attendue vers les valeurs refuges (or, dollar, volatilité) ; pression sur les indices et la crypto."
      : niveau < -0.25
        ? "Rotation attendue vers les actifs de risque ; pression sur les valeurs refuges."
        : "Aucun biais directionnel marqué.";

  return (
    <section>
      <h2>Contexte mondial</h2>
      <div className="climate">
        <div className="climate-head">
          <span
            className={niveau > 0.25 ? "down" : niveau < -0.25 ? "up" : "muted"}
            style={{ fontSize: 22, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
          >
            {niveau > 0 ? "+" : ""}
            {fmtNum(niveau)}
          </span>
          <div>
            <strong>{titre}</strong>
            <div className="muted" style={{ fontSize: 12 }}>
              {lecture}
            </div>
          </div>
        </div>
        <div className="tablewrap" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th className="num">Tension</th>
                <th>Fait marquant</th>
                <th>Source</th>
                <th>Termes</th>
              </tr>
            </thead>
            <tbody>
              {riskOff.top.slice(0, 6).map((t, i) => (
                <tr key={i}>
                  <td className={`num ${t.risk > 0 ? "down" : "up"}`}>
                    {t.risk > 0 ? "+" : ""}
                    {fmtNum(t.risk, 1)}
                  </td>
                  <td className="wide">
                    {t.url ? (
                      <a href={t.url} target="_blank" rel="noopener noreferrer">
                        {t.title}
                      </a>
                    ) : (
                      t.title
                    )}
                  </td>
                  <td className="muted">{t.source}</td>
                  <td className="muted">
                    {t.terms.slice(0, 3).map((x) => (
                      <span key={x} className="pill" style={{ marginRight: 4 }}>
                        {x}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note">
          Indice calculé par lexique pondéré sur {riskOff.count} article(s)
          porteurs, en anglais et en français, sur une échelle de −1 (apaisement)
          à +1 (escalade). Il est ensuite appliqué à chaque actif via son bêta de
          valeur refuge : une escalade fait monter l’or et la volatilité, et pèse
          sur les indices et la crypto.
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Agenda                                                              */
/* ------------------------------------------------------------------ */
export function Agenda({
  agenda,
}: {
  agenda?: {
    mechanical: {
      date: string;
      days_ahead: number;
      label: string;
      impact: string;
      detail: string;
    }[];
    press: { label: string; impact: string; detail: string; source: string; url: string }[];
  };
}) {
  if (!agenda) return null;
  const { mechanical = [], press = [] } = agenda;
  if (!mechanical.length && !press.length) return null;

  const badge = (impact: string) =>
    impact === "eleve" ? "short" : impact === "moyen" ? "neutre" : "neutre";
  const libelle = (impact: string) =>
    impact === "eleve" ? "IMPACT ÉLEVÉ" : impact === "moyen" ? "IMPACT MOYEN" : "IMPACT FAIBLE";

  return (
    <section>
      <h2>À venir</h2>

      {mechanical.length > 0 && (
        <div className="agenda">
          {mechanical.map((e, i) => (
            <div className="event" key={i}>
              <div className="event-when">
                <div className="event-days">
                  {e.days_ahead === 0 ? "auj." : `J+${e.days_ahead}`}
                </div>
                <div className="event-date muted">{e.date.slice(5)}</div>
              </div>
              <div>
                <div className="event-head">
                  <strong>{e.label}</strong>
                  <span className={`badge ${badge(e.impact)}`}>{libelle(e.impact)}</span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>
                  {e.detail}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {press.length > 0 && (
        <>
          <h3 style={{ marginTop: 22 }}>Annoncé par la presse</h3>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Échéance</th>
                  <th>Impact</th>
                  <th>Ce que dit la source</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {press.map((e, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{e.label}</td>
                    <td className={e.impact === "eleve" ? "down" : "muted"}>
                      {e.impact === "eleve" ? "élevé" : e.impact === "moyen" ? "moyen" : "faible"}
                    </td>
                    <td className="wide">
                      {e.url ? (
                        <a href={e.url} target="_blank" rel="noopener noreferrer">
                          {e.detail}
                        </a>
                      ) : (
                        e.detail
                      )}
                    </td>
                    <td className="muted">{e.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <p className="note">
        Deux sources, et deux seulement. Les échéances du haut se{" "}
        <strong>déduisent du calendrier</strong> par une règle — le rapport sur
        l’emploi tombe le premier vendredi du mois, les options expirent le
        troisième — et sont donc exactes par construction. Celles du bas sont
        celles que <strong>la presse annonce</strong>, citées avec leur source
        pour être vérifiables. Aucun calendrier de réunions de banques centrales
        n’est inscrit en dur : une date fausse présentée comme certaine serait
        pire que pas de date du tout.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Validation historique                                               */
/* ------------------------------------------------------------------ */
export function Validation({ bt }: { bt?: Backtest }) {
  if (!bt?.calibration?.trades) return null;
  const c = bt.calibration;
  const positif = c.esperance_realisee > 0;

  return (
    <section>
      <h2>Validation historique</h2>
      <p style={{ maxWidth: "76ch", marginBottom: 14 }}>
        Le moteur est rejoué bougie par bougie sur {bt.parametres.bars} bougies
        d’historique et {bt.parametres.actifs} actifs, sans qu’aucune donnée
        future ne puisse entrer dans la décision. Le marché tranche ensuite sur
        les bougies suivantes. C’est la seule mesure qui dise si le système a un
        avantage réel.
      </p>

      <div className="kpis">
        <div className="kpi">
          <div className="kpi-value">{c.trades}</div>
          <div className="kpi-label">Trades simulés</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">{fmtNum(c.win_rate_global, 1)} %</div>
          <div className="kpi-label">Réussite observée</div>
        </div>
        <div className="kpi">
          <div className="kpi-value muted">{fmtNum(c.prob_predite_moyenne, 1)} %</div>
          <div className="kpi-label">Prédite par le modèle</div>
        </div>
        <div className={`kpi`}>
          <div className={`kpi-value ${positif ? "up" : "down"}`}>
            {c.esperance_realisee >= 0 ? "+" : ""}
            {fmtNum(c.esperance_realisee, 3)} R
          </div>
          <div className="kpi-label">Espérance réalisée</div>
        </div>
        <div className="kpi">
          <div className="kpi-value">
            {c.facteur_de_profit === null ? "—" : fmtNum(c.facteur_de_profit, 2)}
          </div>
          <div className="kpi-label">Facteur de profit</div>
        </div>
        <div className="kpi">
          <div className="kpi-value down">
            {c.drawdown_max_R ? `${fmtNum(c.drawdown_max_R, 1)} R` : "—"}
          </div>
          <div className="kpi-label">Drawdown max</div>
        </div>
      </div>

      {c.verdict && (
        <div className={c.significatif ? "warn" : "verdict"} style={{ maxWidth: "76ch" }}>
          <strong>Verdict statistique :</strong> {c.verdict}
          {c.ic95 && (
            <>
              {" "}Intervalle de confiance à 95 % :{" "}
              <code>
                [{c.ic95[0] >= 0 ? "+" : ""}
                {fmtNum(c.ic95[0], 3)} ; {c.ic95[1] >= 0 ? "+" : ""}
                {fmtNum(c.ic95[1], 3)}] R
              </code>
              {c.ic95[0] < 0 && c.ic95[1] > 0 && (
                <> — l’intervalle contient zéro, donc aucun avantage n’est démontré.</>
              )}
            </>
          )}
        </div>
      )}

      {c.par_tranche_de_score && c.par_tranche_de_score.length > 0 && (
        <>
          <h3 style={{ marginTop: 24 }}>Le score discrimine-t-il ?</h3>
          <div className="charts" style={{ marginBottom: 16 }}>
            <Chart
              wide
              title="Réussite prédite contre réussite observée"
              sub="par tranche de score"
              foot={
                <>
                  L’alignement des deux barres dit si le modèle sait annoncer
                  sa propre probabilité de gain. Leur progression d’une tranche
                  à l’autre dit si une conviction plus forte donne un meilleur
                  résultat. Ce sont deux propriétés indépendantes, et seule la
                  seconde justifie l’existence d’un seuil.
                </>
              }
            >
              <Calibration
                points={calibration(bt)}
                seuil={bt.effet_de_la_structure?.adosse_a_la_structure?.seuil_rentabilite}
                w={860}
                h={220}
              />
            </Chart>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Tranche de score</th>
                  <th className="num">Trades</th>
                  <th className="num">Réussite</th>
                  <th className="num">Prédite</th>
                  <th className="num">Espérance réalisée</th>
                </tr>
              </thead>
              <tbody>
                {c.par_tranche_de_score.map((t) => (
                  <tr key={t.tranche}>
                    <td style={{ fontWeight: 600 }}>{t.tranche}</td>
                    <td className="num">{t.trades}</td>
                    <td className="num">{fmtNum(t.win_rate, 1)} %</td>
                    <td className="num muted">{fmtNum(t.prob_predite, 1)} %</td>
                    <td className={`num ${t.esperance_realisee > 0 ? "up" : "down"}`}>
                      {t.esperance_realisee >= 0 ? "+" : ""}
                      {fmtNum(t.esperance_realisee, 3)} R
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Discriminant bt={bt} />
        </>
      )}

      <p className="note">
        {bt.limites.map((l, i) => (
          <span key={i}>
            {l}
            <br />
          </span>
        ))}
      </p>
    </section>
  );
}

/**
 * Le score discrimine-t-il, mesuré sur les trades et non sur les tranches.
 *
 * Le moteur publie une corrélation calculée sur les tranches agrégées. Quand
 * il n'y en a que deux — le cas courant — un Spearman vaut toujours
 * exactement ±1 : le chiffre a l'allure d'une mesure fine alors qu'il ne peut
 * prendre que deux valeurs, et il resterait à ±1 même si le moteur était
 * parfait. Affiché tel quel, il trompe dans les deux sens.
 *
 * On recalcule donc trade par trade, où le coefficient a un sens, un
 * intervalle de confiance et un t.
 */
function Discriminant({ bt }: { bt: Backtest }) {
  const d = discrimination(bt);
  if (!d) return null;
  const negatif = d.rho < 0;
  return (
    <p className="note">
      Corrélation de rang entre le score et le résultat, mesurée sur les{" "}
      <strong>{d.n} trades individuels</strong> et non sur les tranches
      agrégées&nbsp;:{" "}
      <strong className={negatif ? "down" : d.rho > 0.1 ? "up" : undefined}>
        {d.rho >= 0 ? "+" : ""}
        {fmtNum(d.rho, 3)}
      </strong>{" "}
      <span className="muted">
        (±{fmtNum(d.marge, 3)} à 95 %, t&nbsp;= {fmtNum(d.t, 2)})
      </span>
      .{" "}
      {!d.significatif ? (
        <>
          L’échantillon ne permet pas de distinguer ce coefficient du bruit : le
          seuil n’est ni justifié ni invalidé par cette mesure.
        </>
      ) : negatif ? (
        <>
          <strong>
            Le coefficient est négatif et distinguable du bruit : une conviction
            plus élevée est suivie d’un résultat plus mauvais.
          </strong>{" "}
          C’est la faiblesse la plus sérieuse du moteur. Elle a une conséquence
          directe sur l’usage : au-dessus du seuil de publication, ce sont les
          trades les moins fiables qui sont mis en avant.
        </>
      ) : (
        <>
          Une conviction plus élevée s’accompagne d’un meilleur résultat, ce qui
          est la propriété qui justifie l’existence d’un seuil.
        </>
      )}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Pouvoir prédictif des facteurs                                      */
/* ------------------------------------------------------------------ */
const HORIZONS = ["h6", "h12", "h24", "h48"];

export function FactorPower({ probe }: { probe?: Probe }) {
  const facteurs = probe?.coefficients?.par_facteur;
  if (!facteurs || !Object.keys(facteurs).length) return null;

  const lignes = Object.entries(facteurs).sort(
    (a, b) => Math.abs(b[1].ic_max) - Math.abs(a[1].ic_max),
  );

  return (
    <section>
      <h2>Pouvoir prédictif des facteurs</h2>
      <p style={{ maxWidth: "76ch", marginBottom: 14 }}>
        À chaque pas et <strong>sans aucun filtre</strong>, la valeur de chaque
        facteur est enregistrée avec le rendement effectivement réalisé
        ensuite, normalisé par l’ATR. Le coefficient d’information est la
        corrélation de rang entre les deux : il dit si le facteur porte une
        information, indépendamment du reste du moteur.{" "}
        {probe.coefficients.observations.toLocaleString("fr-FR")} observations
        sur {probe.parametres.actifs} actifs.
      </p>

      <PetitesMultiplesIC courbes={courbesIC(probe)} />

      <p className="note" style={{ marginBottom: 16 }}>
        Un cadre par facteur, tous à la même échelle verticale : recadrer
        chacun sur ses propres bornes ferait paraître le bruit aussi ample que
        le signal. En abscisse l’horizon en bougies, en ordonnée le
        coefficient d’information. Les points pleins se distinguent du bruit.
      </p>

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Facteur</th>
              {HORIZONS.map((h) => (
                <th key={h} className="num">
                  {h.replace("h", "")} bougies
                </th>
              ))}
              <th>Lecture</th>
            </tr>
          </thead>
          <tbody>
            {lignes.map(([nom, v]) => (
              <tr key={nom}>
                <td style={{ fontWeight: 600 }}>{nom.replace("_", " ")}</td>
                {HORIZONS.map((h) => {
                  const e = v.horizons[h];
                  if (!e) return <td key={h} className="num muted">—</td>;
                  return (
                    <td
                      key={h}
                      className={`num ${
                        !e.significatif ? "muted" : e.ic > 0 ? "up" : "down"
                      }`}
                    >
                      {e.ic >= 0 ? "+" : ""}
                      {fmtNum(e.ic, 4)}
                      {e.significatif ? " *" : ""}
                    </td>
                  );
                })}
                <td className="muted wide">
                  {!v.significatif
                    ? "aucune information mesurable — poids nul"
                    : v.ic_max < 0
                      ? "prédit à l’envers — le poids est négatif"
                      : "prédit dans le bon sens"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="note">
        <strong>*</strong> = statistiquement distinguable du bruit (|t| &gt; 2).
        Un coefficient de 0.02 à 0.05 est exploitable en gestion quantitative,
        au-delà de 0.10 il est inhabituel, un coefficient nul signifie que le
        facteur est du bruit.
        <br />
        <br />
        Cette mesure a renversé la conception du moteur. Les trois facteurs de
        suivi de tendance — tendance, structure, cassure — ressortent{" "}
        <strong>négatifs et significatifs</strong> : une lecture haussière du
        prix est suivie, en moyenne, d’un rendement négatif. Le seul facteur
        dont le signe était correct est le retour à la moyenne. Les
        pondérations ont été réécrites à partir de ces coefficients au lieu
        d’être supposées, et le score composite est passé d’un pouvoir
        prédictif nul à un coefficient de +0.063 à 48 bougies.
        <br />
        <br />
        {probe.note}
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Redevabilité                                                        */
/* ------------------------------------------------------------------ */

const ISSUES: Record<Issue, { libelle: string; classe: string }> = {
  cible: { libelle: "objectif atteint", classe: "cible" },
  stop: { libelle: "stop touché", classe: "stop" },
  expiration: { libelle: "expiré", classe: "neutre" },
  en_cours: { libelle: "en cours", classe: "encours" },
  hors_portee: { libelle: "hors portée", classe: "neutre" },
  indetermine: { libelle: "indéterminé", classe: "neutre" },
};

const dateCourte = (iso: string) =>
  new Date(iso).toLocaleDateString("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

/**
 * Ce qu'ont donné les signaux réellement émis.
 *
 * Toutes les autres mesures du site portent sur des trades que personne n'a
 * vus : le backtest rejoue le passé, le portefeuille papier n'ouvre qu'une
 * fraction des signaux et sous des plafonds de risque. Cette section est la
 * seule qui porte sur ce qui a été **publié** — et donc la seule qu'un lecteur
 * puisse confronter à ce qu'il a lu au moment où il l'a lu.
 *
 * Elle est délibérément placée juste après les configurations retenues :
 * l'ordre de lecture est « voici ce que le moteur propose aujourd'hui », puis
 * « voici ce que ses propositions ont valu jusqu'ici ».
 */
export function Redevabilite({ suivi }: { suivi?: Suivi | null }) {
  if (!suivi?.signaux?.length) return null;
  const r = suivi.resume;
  const cumul = cumulSuivi(suivi);
  const tranches = r.tranches;

  // Concentration. Un bilan de neuf signaux dont sept portent sur le même
  // actif n'est pas un bilan de moteur, c'est un bilan sur cet actif — et la
  // moyenne d'ensemble le cache au lieu de le dire.
  const parActif = new Map<string, number>();
  for (const s of suivi.signaux) {
    parActif.set(s.symbol, (parActif.get(s.symbol) ?? 0) + 1);
  }
  const [dominant, nDominant] = [...parActif.entries()].sort((a, b) => b[1] - a[1])[0];
  const concentre = nDominant / suivi.signaux.length >= 0.5;

  return (
    <section>
      <h2>Ce qu’ont donné les signaux émis</h2>
      <p className="note" style={{ marginTop: 0, marginBottom: 14 }}>
        Le rejeu et le portefeuille papier mesurent des trades que personne n’a
        vus. Ce tableau-ci ne contient que des signaux <strong>réellement
        émis</strong>, à la date où ils l’ont été, avec l’issue que le marché
        leur a donnée ensuite. C’est la seule mesure de ce site qu’un lecteur
        puisse confronter à ce qu’il avait sous les yeux.
      </p>

      <div className="kpis">
        <Kpi label="Signaux" value={String(r.signaux)} />
        <Kpi label="Émissions" value={String(r.emissions)} />
        <Kpi label="Publiés sur Discord" value={String(r.publies_discord)} />
        <Kpi label="Tranchés" value={String(tranches)} />
        <Kpi
          label="Réussite"
          value={r.win_rate === null ? "—" : `${fmtNum(r.win_rate, 0)} %`}
        />
        <Kpi
          label="Espérance"
          value={
            r.esperance_r === null
              ? "—"
              : `${r.esperance_r >= 0 ? "+" : ""}${fmtNum(r.esperance_r, 3)} R`
          }
          tone={r.esperance_r === null ? undefined : r.esperance_r > 0 ? "up" : "down"}
        />
      </div>

      {!r.significatif && (
        <div className="warn" style={{ maxWidth: "76ch" }}>
          <strong>
            {tranches > 1
              ? `${tranches} signaux tranchés ne mesurent rien.`
              : `${tranches} signal tranché ne mesure rien.`}
          </strong>{" "}
          Il en faudrait une trentaine pour qu’un taux de réussite cesse d’être
          du bruit. Les chiffres ci-dessus sont affichés parce qu’ils sont
          vérifiables, pas parce qu’ils prouvent quoi que ce soit — et ils ne
          contredisent pas le rejeu, qui porte sur un tout autre échantillon.
          {concentre && (
            <>
              {" "}
              <strong>
                {nDominant} de ces {suivi.signaux.length} signaux portent sur{" "}
                {dominant}
              </strong>{" "}
              : ce bilan décrit surtout le comportement du moteur sur un actif,
              pas sur l’univers.
            </>
          )}
        </div>
      )}

      <p className="note" style={{ maxWidth: "76ch" }}>
        Le moteur réémet le même signal à chaque scan tant que la configuration
        tient : <strong>{r.emissions} émissions</strong> ne font que{" "}
        <strong>{r.signaux} signaux</strong>. Les émissions espacées de moins de{" "}
        {suivi.fenetre_regroupement_min} minutes — le délai anti-spam Discord —
        sont regroupées, parce qu’elles n’ont pas pu produire deux alertes
        distinctes. Le plan retenu est celui de la première : c’est le prix
        qu’aurait obtenu quelqu’un qui a agi sur l’alerte.
      </p>

      {cumul && cumul.points.length > 1 && (
        <div className="charts" style={{ marginTop: 16 }}>
          <Chart
            wide
            title="R cumulés des signaux émis"
            sub={`${cumul.gagnants} gagnant(s) · ${cumul.perdants} perdant(s)`}
            foot="Dans l’ordre où le marché les a tranchés. Un signal encore en cours n’y figure pas : il n’a pas de résultat."
          >
            <CourbeCumulR serie={cumul} h={180} seed="suivi" />
          </Chart>
        </div>
      )}

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Émis le</th>
              <th>Symbole</th>
              <th>Sens</th>
              <th className="num">Score</th>
              <th className="num">Entrée</th>
              <th className="num">Stop</th>
              <th className="num">Objectif</th>
              <th>Issue</th>
              <th className="num">Résultat</th>
              <th className="num">MFE / MAE</th>
            </tr>
          </thead>
          <tbody>
            {suivi.signaux.map((s) => {
              const issue = ISSUES[s.issue] ?? ISSUES.indetermine;
              return (
                <tr key={s.id}>
                  <td className="muted">
                    {dateCourte(s.premiere_emission)}
                    {s.emissions > 1 && (
                      <span className="pill" style={{ marginLeft: 6 }}>
                        ×{s.emissions}
                      </span>
                    )}
                  </td>
                  <td style={{ fontWeight: 600 }}>
                    <a href={`/actif/${encodeURIComponent(s.symbol)}`}>{s.symbol}</a>
                  </td>
                  <td className={s.direction === "long" ? "up" : "down"}>
                    {s.direction === "long" ? "achat" : "vente"}
                  </td>
                  <td className="num">
                    {s.score.toFixed(0)}
                    {s.alerte_discord && (
                      <span className="pill" style={{ marginLeft: 6 }} title={`Publié sur Discord : score au-delà de ${suivi.seuil_alerte}`}>
                        discord
                      </span>
                    )}
                  </td>
                  <td className="num">{fmtPrice(s.entry)}</td>
                  <td className="num">{fmtPrice(s.stop)}</td>
                  <td className="num">{fmtPrice(s.target)}</td>
                  <td>
                    <span className={`issue ${issue.classe}`}>{issue.libelle}</span>
                  </td>
                  <td
                    className={`num ${
                      s.r_multiple === null ? "muted" : s.r_multiple > 0 ? "up" : "down"
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
                  <td className="num muted">
                    <span className="up">+{fmtNum(s.mfe, 2)}</span> /{" "}
                    <span className="down">{fmtNum(s.mae, 2)}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="note">
        Un résultat entre parenthèses est un trade encore ouvert, valorisé au
        dernier prix connu : il n’entre dans aucune statistique. Les issues sont
        déterminées sur les bougies horaires suivant l’émission, avec les règles
        du rejeu — le stop l’emporte quand une même bougie touche le stop et
        l’objectif, l’horizon est borné à {suivi.horizon_bougies} bougies, et
        les frais sont retranchés à l’entrée comme à la sortie. Une issue
        établie n’est jamais recalculée : elle est figée dans l’historique du
        dépôt, toute réécriture après coup y serait visible.
      </p>
    </section>
  );
}
