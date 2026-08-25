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
  type Report,
  type RiskOff,
  type Signal,
  type Speech,
  type Trade,
} from "@/lib/data";

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
              <th className="num">P(gain)</th>
              <th className="num">Espérance</th>
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
