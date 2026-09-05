/**
 * Graphiques.
 *
 * Tout est rendu côté serveur en SVG : aucune bibliothèque, aucun octet de
 * JavaScript envoyé au navigateur pour afficher une courbe. Les couleurs sont
 * prises dans les variables CSS via des classes, donc le passage en thème
 * sombre redessine les tracés sans recalcul.
 *
 * Convention commune : la boîte de dessin est exprimée en unités de vue, la
 * marge réserve la place des graduations, et l'axe des ordonnées est gradué
 * en valeurs rondes. Aucun graphique n'invente de point : quand la série est
 * vide, le composant ne rend rien plutôt que d'afficher un cadre vide.
 */
import {
  areaPath,
  extent,
  gradId,
  linePath,
  niceScale,
  polyLength,
  scaler,
  smoothPath,
  type Pt,
} from "@/lib/chart";
import type {
  CourbeIC,
  CumulSerie,
  EquiteSerie,
  Panier,
  PointCalibration,
  PointExcursion,
  PointUnivers,
} from "@/lib/series";

type Marge = { t: number; r: number; b: number; l: number };

const num = (v: number, d = 0) =>
  v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });

const jour = (iso?: string) =>
  iso
    ? new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })
    : "";

/* ------------------------------------------------------------------ */
/* Enveloppe                                                           */
/* ------------------------------------------------------------------ */

export function Chart({
  title,
  sub,
  foot,
  wide,
  children,
}: {
  title: string;
  sub?: string;
  foot?: React.ReactNode;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <figure className={`chart${wide ? " wide" : ""}`}>
      <figcaption className="chart-head">
        <span className="chart-title">{title}</span>
        {sub && <span className="chart-sub">{sub}</span>}
      </figcaption>
      <div className="chart-body">{children}</div>
      {foot && <div className="chart-foot">{foot}</div>}
    </figure>
  );
}

export function Legend({
  items,
}: {
  items: { label: string; tone?: "up" | "down" | "ghost"; block?: boolean }[];
}) {
  return (
    <div className="chart-legend">
      {items.map((i) => (
        <span className="legend-item" key={i.label}>
          <span
            className={`legend-swatch${i.tone ? ` ${i.tone}` : ""}${i.block ? " block" : ""}`}
          />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Grille                                                              */
/* ------------------------------------------------------------------ */

function GrilleY({
  ticks,
  sy,
  x0,
  x1,
  format,
  zero,
}: {
  ticks: number[];
  sy: (v: number) => number;
  x0: number;
  x1: number;
  format: (v: number) => string;
  zero?: number;
}) {
  return (
    <g aria-hidden="true">
      {ticks.map((t) => {
        const y = Math.round(sy(t)) + 0.5;
        const estZero = zero !== undefined && Math.abs(t - zero) < 1e-9;
        return (
          <g key={t}>
            <line
              className={estZero ? "c-zero" : "c-grid"}
              x1={x0}
              x2={x1}
              y1={y}
              y2={y}
            />
            <text className="c-tick" x={x0 - 6} y={y + 3} textAnchor="end">
              {format(t)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/* ------------------------------------------------------------------ */
/* Courbe étincelle                                                    */
/* ------------------------------------------------------------------ */

/** Miniature sans axe : la forme, rien d'autre. Utilisée dans les KPI. */
export function Sparkline({
  values,
  w = 120,
  h = 24,
  tone,
  seed = "sp",
}: {
  values: number[];
  w?: number;
  h?: number;
  tone?: "up" | "down";
  seed?: string;
}) {
  if (values.length < 2) return null;
  const [lo, hi] = extent(values);
  const sx = scaler(0, values.length - 1, 1, w - 1);
  const sy = scaler(lo, hi, h - 2, 2);
  const pts: Pt[] = values.map((v, i) => ({ x: sx(i), y: sy(v) }));
  const id = gradId("sp", seed + values.length);
  const cls = tone ? ` ${tone}` : "";
  const teinte = `var(--${tone ?? "ink"})`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} role="presentation" preserveAspectRatio="none">
      <defs>
        {/* Le dégradé nomme sa teinte : `currentColor` dans un `stop` se
            résout sur l'élément de dégradé lui-même, pas sur le tracé qui le
            référence — il rendrait l'aire grise quelle que soit la courbe. */}
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={teinte} stopOpacity="0.2" />
          <stop offset="100%" stopColor={teinte} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path className="c-area" d={areaPath(pts, h)} fill={`url(#${id})`} />
      <path className={`c-line thin${cls}`} d={linePath(pts)} />
      <circle
        className={`c-dot${cls}`}
        cx={pts[pts.length - 1].x}
        cy={pts[pts.length - 1].y}
        r={2}
      />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Courbe de capital                                                   */
/* ------------------------------------------------------------------ */

/**
 * Capital du portefeuille papier, avec son repli en dessous.
 *
 * Les deux panneaux partagent l'axe horizontal : un creux de la courbe haute
 * et le creux correspondant du repli tombent exactement à la même abscisse.
 * Afficher le repli séparément, ou pas du tout, laisserait croire qu'une
 * hausse de fin de période a effacé le chemin parcouru pour y arriver.
 */
export function CourbeCapital({
  serie,
  w = 860,
  h = 240,
  hRepli = 66,
}: {
  serie: EquiteSerie;
  w?: number;
  h?: number;
  hRepli?: number;
}) {
  const { points, repli, initial } = serie;
  if (points.length < 2) return null;

  const m: Marge = { t: 12, r: 56, b: 22, l: 54 };
  const bas = h - m.b;
  const [lo, hi] = extent([...points.map((p) => p.v), initial]);
  const ech = niceScale(lo, hi, 4);
  const sx = scaler(0, points.length - 1, m.l, w - m.r);
  const sy = scaler(ech.min, ech.max, bas, m.t);

  const pts: Pt[] = points.map((p) => ({ x: sx(p.i), y: sy(p.v) }));
  const gagne = serie.final >= initial;
  const ton = gagne ? "up" : "down";
  const id = gradId("eq", `${points.length}-${serie.final}`);

  // Panneau de repli, sous le premier, avec la même abscisse.
  const hautDD = h + 10;
  const basDD = hautDD + hRepli;
  const pireRepli = Math.min(...repli.map((r) => r.v), -1e-9);
  const echDD = niceScale(pireRepli, 0, 2);
  const syDD = scaler(echDD.min, echDD.max, basDD, hautDD + 8);
  const ptsDD: Pt[] = repli.map((p) => ({ x: sx(p.i), y: syDD(p.v) }));

  const dernier = pts[pts.length - 1];
  const total = basDD + 20;

  return (
    <svg
      viewBox={`0 0 ${w} ${total}`}
      role="img"
      aria-label={`Capital du portefeuille papier, de ${num(initial)} à ${num(serie.final)}, repli maximal ${num(serie.repli_max)}`}
    >
      <defs>
        {/* Voir la note du même dégradé dans `Sparkline` : la teinte est
            nommée, jamais héritée. */}
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={`var(--${ton})`} stopOpacity="0.2" />
          <stop offset="100%" stopColor={`var(--${ton})`} stopOpacity="0" />
        </linearGradient>
      </defs>

      <GrilleY
        ticks={ech.ticks}
        sy={sy}
        x0={m.l}
        x1={w - m.r}
        format={(v) => num(v)}
      />

      {/* Capital de départ : la seule référence qui compte pour dire si le
          portefeuille a gagné quelque chose. */}
      <line
        className="c-zero"
        x1={m.l}
        x2={w - m.r}
        y1={Math.round(sy(initial)) + 0.5}
        y2={Math.round(sy(initial)) + 0.5}
      />
      <text className="c-tick" x={w - m.r + 6} y={sy(initial) + 3}>
        départ
      </text>

      <path className="c-area" d={areaPath(pts, bas, true)} fill={`url(#${id})`} />
      <path
        className={`c-line ${ton} c-draw`}
        d={smoothPath(pts)}
        style={{ "--len": polyLength(pts) } as React.CSSProperties}
      />
      <g className="c-fade">
        <circle className={`c-dot ${ton}`} cx={dernier.x} cy={dernier.y} r={3} />
        <text
          className="c-tick"
          x={w - m.r + 6}
          y={dernier.y + 3}
          style={{ fill: `var(--${ton})` }}
        >
          {num(serie.final)}
        </text>
      </g>

      <line className="c-axis" x1={m.l} x2={w - m.r} y1={bas + 0.5} y2={bas + 0.5} />
      {[0, Math.floor((points.length - 1) / 2), points.length - 1].map((i, k) => (
        <text
          key={i}
          className="c-tick"
          x={sx(i)}
          y={bas + 14}
          textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}
        >
          {jour(points[i]?.t)}
        </text>
      ))}

      {/* Repli */}
      <text className="c-label" x={m.l} y={hautDD + 2}>
        repli depuis le sommet
      </text>
      <line className="c-grid" x1={m.l} x2={w - m.r} y1={hautDD + 8.5} y2={hautDD + 8.5} />
      <path
        className="c-area"
        d={areaPath(ptsDD, syDD(0))}
        style={{ fill: "var(--down)", opacity: 0.14 }}
      />
      <path className="c-line down thin" d={linePath(ptsDD)} />
      <text className="c-tick" x={m.l - 6} y={basDD + 3} textAnchor="end">
        {num(echDD.min)}
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Courbe de R cumulés                                                 */
/* ------------------------------------------------------------------ */

/**
 * R cumulés, trade après trade.
 *
 * L'aire est découpée à la ligne de zéro par deux masques : au-dessus elle
 * est verte, en dessous rouge. Un dégradé unique du haut vers le bas aurait
 * été plus simple, mais il aurait teinté en vert une portion de courbe
 * située sous zéro — soit exactement l'inverse de ce qu'elle raconte.
 */
export function CourbeCumulR({
  serie,
  w = 860,
  h = 220,
  label = "R cumulés",
  seed = "cr",
}: {
  serie: CumulSerie;
  w?: number;
  h?: number;
  label?: string;
  seed?: string;
}) {
  const { points } = serie;
  if (points.length < 2) return null;

  const m: Marge = { t: 12, r: 46, b: 24, l: 44 };
  const bas = h - m.b;
  const [lo, hi] = extent(points.map((p) => p.v));
  const ech = niceScale(Math.min(lo, 0), Math.max(hi, 0), 4);
  const sx = scaler(0, points.length - 1, m.l, w - m.r);
  const sy = scaler(ech.min, ech.max, bas, m.t);
  const yZero = sy(0);

  const pts: Pt[] = points.map((p) => ({ x: sx(p.i), y: sy(p.v) }));
  const aire = areaPath(pts, yZero);
  const haut = gradId("ch", seed);
  const basId = gradId("cb", seed);
  const dernier = pts[pts.length - 1];
  const positif = serie.total >= 0;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`${label} sur ${points.length - 1} trades, total ${num(serie.total, 2)} R, repli maximal ${num(serie.repli_max, 2)} R`}
    >
      <defs>
        <clipPath id={haut}>
          <rect x={0} y={0} width={w} height={Math.max(0, yZero)} />
        </clipPath>
        <clipPath id={basId}>
          <rect x={0} y={yZero} width={w} height={Math.max(0, h - yZero)} />
        </clipPath>
      </defs>

      <GrilleY
        ticks={ech.ticks}
        sy={sy}
        x0={m.l}
        x1={w - m.r}
        format={(v) => num(v, Math.abs(v) < 10 ? 1 : 0)}
        zero={0}
      />

      <path d={aire} clipPath={`url(#${haut})`} style={{ fill: "var(--up)", opacity: 0.13 }} />
      <path d={aire} clipPath={`url(#${basId})`} style={{ fill: "var(--down)", opacity: 0.13 }} />

      <path
        className="c-line c-draw"
        d={linePath(pts)}
        clipPath={`url(#${haut})`}
        style={{ "--len": polyLength(pts), stroke: "var(--up)" } as React.CSSProperties}
      />
      <path
        className="c-line c-draw"
        d={linePath(pts)}
        clipPath={`url(#${basId})`}
        style={{ "--len": polyLength(pts), stroke: "var(--down)" } as React.CSSProperties}
      />

      <g className="c-fade">
        <circle
          className={`c-dot ${positif ? "up" : "down"}`}
          cx={dernier.x}
          cy={dernier.y}
          r={3}
        />
        <text
          className="c-tick"
          x={w - m.r + 6}
          y={dernier.y + 3}
          style={{ fill: `var(--${positif ? "up" : "down"})` }}
        >
          {serie.total >= 0 ? "+" : ""}
          {num(serie.total, 1)}
        </text>
      </g>

      <line className="c-axis" x1={m.l} x2={w - m.r} y1={bas + 0.5} y2={bas + 0.5} />
      <text className="c-tick" x={m.l} y={bas + 15}>
        1
      </text>
      <text className="c-tick" x={w - m.r} y={bas + 15} textAnchor="end">
        {points.length - 1}
      </text>
      <text className="c-label" x={(m.l + w - m.r) / 2} y={bas + 16} textAnchor="middle">
        numéro de trade
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Distribution                                                        */
/* ------------------------------------------------------------------ */

/** Histogramme des R réalisés, découpé à zéro. */
export function Distribution({
  paniers,
  w = 420,
  h = 190,
}: {
  paniers: Panier[];
  w?: number;
  h?: number;
}) {
  if (!paniers.length) return null;
  const m: Marge = { t: 10, r: 12, b: 26, l: 32 };
  const bas = h - m.b;
  const maxN = Math.max(...paniers.map((p) => p.n), 1);
  const ech = niceScale(0, maxN, 3);
  const x0 = paniers[0].x0;
  const x1 = paniers[paniers.length - 1].x1;
  const sx = scaler(x0, x1, m.l, w - m.r);
  const sy = scaler(0, ech.max, bas, m.t);
  const largeur = Math.max(1, sx(paniers[0].x1) - sx(paniers[0].x0) - 1.5);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Distribution des résultats en R sur ${paniers.reduce((a, p) => a + p.n, 0)} trades`}
    >
      <GrilleY ticks={ech.ticks} sy={sy} x0={m.l} x1={w - m.r} format={(v) => num(v)} />
      {paniers.map((p, i) => {
        const centre = (p.x0 + p.x1) / 2;
        const y = sy(p.n);
        return (
          <rect
            key={i}
            className={`c-bar ${centre >= 0 ? "up" : "down"}`}
            x={sx(p.x0) + 0.75}
            y={y}
            width={largeur}
            height={Math.max(0, bas - y)}
            rx={1}
          />
        );
      })}
      <line
        className="c-zero"
        x1={Math.round(sx(0)) + 0.5}
        x2={Math.round(sx(0)) + 0.5}
        y1={m.t}
        y2={bas}
      />
      <line className="c-axis" x1={m.l} x2={w - m.r} y1={bas + 0.5} y2={bas + 0.5} />
      {[x0, 0, x1].map((v, i) => (
        <text
          key={i}
          className="c-tick"
          x={sx(v)}
          y={bas + 14}
          textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
        >
          {num(v, 1)}
        </text>
      ))}
      <text className="c-label" x={w - m.r} y={bas + 24} textAnchor="end">
        résultat en R
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Excursions                                                          */
/* ------------------------------------------------------------------ */

/** Nuage MFE / MAE : chaque point est un trade. */
export function NuageExcursions({
  points,
  w = 420,
  h = 300,
}: {
  points: PointExcursion[];
  w?: number;
  h?: number;
}) {
  if (!points.length) return null;
  const m: Marge = { t: 22, r: 14, b: 30, l: 38 };
  const bas = h - m.b;
  const echX = niceScale(Math.min(...points.map((p) => p.mae), 0), 0, 3);
  const echY = niceScale(0, Math.max(...points.map((p) => p.mfe), 1), 3);
  const sx = scaler(echX.min, echX.max, m.l, w - m.r);
  const sy = scaler(echY.min, echY.max, bas, m.t);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Excursion favorable contre excursion défavorable, ${points.length} trades`}
    >
      <GrilleY ticks={echY.ticks} sy={sy} x0={m.l} x1={w - m.r} format={(v) => num(v, 1)} />
      {echX.ticks.map((t) => (
        <text key={t} className="c-tick" x={sx(t)} y={bas + 14} textAnchor="middle">
          {num(t, 1)}
        </text>
      ))}
      {/* Seuil du R/R visé : au-dessus, le trade a atteint son objectif. */}
      <line
        className="c-zero"
        x1={m.l}
        x2={w - m.r}
        y1={Math.round(sy(1)) + 0.5}
        y2={Math.round(sy(1)) + 0.5}
      />
      {points.map((p, i) => (
        <circle
          key={i}
          className={p.issue === "cible" ? "c-dot up" : "c-dot down"}
          cx={sx(Math.max(echX.min, p.mae))}
          cy={sy(Math.min(echY.max, p.mfe))}
          r={2.6}
          opacity={0.62}
        >
          <title>
            {p.symbol} · MFE {num(p.mfe, 2)} R · MAE {num(p.mae, 2)} R · {p.issue}
          </title>
        </circle>
      ))}
      <line className="c-axis" x1={m.l} x2={w - m.r} y1={bas + 0.5} y2={bas + 0.5} />
      <text className="c-label" x={w - m.r} y={bas + 25} textAnchor="end">
        pire excursion défavorable (R)
      </text>
      <text className="c-label" x={m.l} y={m.t - 2}>
        meilleure excursion (R)
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Calibration                                                         */
/* ------------------------------------------------------------------ */

/**
 * Réussite prédite contre réussite observée, par tranche de score.
 *
 * Deux barres accolées par tranche, et un trait pour le seuil de rentabilité.
 * Un modèle calibré aligne les deux barres ; un modèle qui discrimine fait
 * monter l'observée avec la tranche. Les deux propriétés sont indépendantes
 * et ce graphique est le seul endroit où on les voit ensemble.
 */
export function Calibration({
  points,
  seuil,
  w = 420,
  h = 220,
}: {
  points: PointCalibration[];
  seuil?: number;
  w?: number;
  h?: number;
}) {
  if (!points.length) return null;
  const m: Marge = { t: 12, r: 14, b: 40, l: 38 };
  const bas = h - m.b;
  const maxV = Math.max(...points.flatMap((p) => [p.predite, p.observee]), seuil ?? 0);
  const ech = niceScale(0, maxV, 3);
  const sy = scaler(0, ech.max, bas, m.t);
  const pas = (w - m.r - m.l) / points.length;
  const largeur = Math.min(26, (pas - 14) / 2);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="Réussite prédite contre réussite observée par tranche de score"
    >
      <GrilleY ticks={ech.ticks} sy={sy} x0={m.l} x1={w - m.r} format={(v) => `${num(v)} %`} />
      {points.map((p, i) => {
        const cx = m.l + pas * i + pas / 2;
        const yP = sy(p.predite);
        const yO = sy(p.observee);
        return (
          <g key={p.tranche}>
            <rect
              className="c-bar faint"
              x={cx - largeur - 2}
              y={yP}
              width={largeur}
              height={Math.max(1, bas - yP)}
              rx={1}
            >
              <title>prédite {num(p.predite, 1)} %</title>
            </rect>
            <rect
              className={`c-bar ${p.observee >= p.predite ? "up" : "down"}`}
              x={cx + 2}
              y={yO}
              width={largeur}
              height={Math.max(1, bas - yO)}
              rx={1}
            >
              <title>observée {num(p.observee, 1)} % sur {p.trades} trades</title>
            </rect>
            <text className="c-tick" x={cx} y={bas + 14} textAnchor="middle">
              {p.tranche}
            </text>
            <text className="c-label" x={cx} y={bas + 26} textAnchor="middle">
              {p.trades} trades
            </text>
          </g>
        );
      })}
      {seuil !== undefined && (
        <g>
          <line
            className="c-zero"
            x1={m.l}
            x2={w - m.r}
            y1={Math.round(sy(seuil)) + 0.5}
            y2={Math.round(sy(seuil)) + 0.5}
          />
          <text className="c-tick" x={w - m.r} y={sy(seuil) - 4} textAnchor="end">
            seuil {num(seuil, 1)} %
          </text>
        </g>
      )}
      <line className="c-axis" x1={m.l} x2={w - m.r} y1={bas + 0.5} y2={bas + 0.5} />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Coefficients d'information                                          */
/* ------------------------------------------------------------------ */

/**
 * Petites multiples : un facteur, un cadre, la même échelle partout.
 *
 * L'échelle commune est le point essentiel. Recalculer les bornes pour chaque
 * facteur ferait paraître un IC de 0,001 aussi ample qu'un IC de 0,06, alors
 * que le premier est du bruit et le second l'un des rares signaux du moteur.
 */
export function PetitesMultiplesIC({
  courbes,
  w = 190,
  h = 108,
}: {
  courbes: CourbeIC[];
  w?: number;
  h?: number;
}) {
  if (!courbes.length) return null;
  // Bornes symétriques calées sur l'amplitude réelle plutôt qu'arrondies :
  // les coefficients d'information vivent entre 0,01 et 0,06, et une échelle
  // « ronde » de ±0,10 écraserait toutes les courbes sur la ligne de zéro.
  const ampleur =
    Math.max(...courbes.flatMap((c) => c.points.map((p) => Math.abs(p.ic))), 0.01) * 1.15;
  const ech = { min: -ampleur, max: ampleur };

  return (
    <div className="charts" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
      {courbes.map((c) => {
        const m: Marge = { t: 16, r: 8, b: 16, l: 34 };
        const bas = h - m.b;
        const bougies = c.points.map((p) => p.bougies);
        const sx = scaler(Math.min(...bougies), Math.max(...bougies), m.l, w - m.r);
        const sy = scaler(ech.min, ech.max, bas, m.t);
        const pts: Pt[] = c.points.map((p) => ({ x: sx(p.bougies), y: sy(p.ic) }));
        const ton = c.ic_max > 0 ? "up" : "down";
        return (
          <div className="multiple" key={c.facteur}>
            <svg
              viewBox={`0 0 ${w} ${h}`}
              role="img"
              aria-label={`Coefficient d'information de ${c.facteur} selon l'horizon`}
            >
              <text className="c-label" x={m.l - 30} y={9} style={{ fontWeight: 600 }}>
                {c.facteur.replace(/_/g, " ")}
              </text>
              <text
                className="c-tick"
                x={w - m.r}
                y={9}
                textAnchor="end"
                style={{ fill: c.significatif ? `var(--${ton})` : "var(--ink-4)" }}
              >
                {c.ic_max >= 0 ? "+" : ""}
                {num(c.ic_max, 3)}
                {c.significatif ? "*" : ""}
              </text>
              <line
                className="c-zero"
                x1={m.l}
                x2={w - m.r}
                y1={Math.round(sy(0)) + 0.5}
                y2={Math.round(sy(0)) + 0.5}
              />
              <text className="c-tick" x={m.l - 5} y={sy(ech.max) + 8} textAnchor="end">
                {num(ech.max, 3)}
              </text>
              <text className="c-tick" x={m.l - 5} y={sy(ech.min)} textAnchor="end">
                {num(ech.min, 3)}
              </text>
              <path
                className={`c-line thin ${c.significatif ? ton : "ghost"}`}
                d={linePath(pts)}
              />
              {c.points.map((p, i) => (
                <circle
                  key={p.horizon}
                  className={`c-dot ${p.significatif ? ton : ""}`}
                  cx={pts[i].x}
                  cy={pts[i].y}
                  r={p.significatif ? 2.4 : 1.6}
                  style={p.significatif ? undefined : { fill: "var(--ink-4)" }}
                >
                  <title>
                    {p.bougies} bougies · IC {num(p.ic, 4)} · t {num(p.t, 2)}
                  </title>
                </circle>
              ))}
              {[c.points[0], c.points[c.points.length - 1]].map((p, i) => (
                <text
                  key={i}
                  className="c-tick"
                  x={sx(p.bougies)}
                  y={h - 4}
                  textAnchor={i === 0 ? "start" : "end"}
                >
                  {p.bougies}
                </text>
              ))}
            </svg>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Barres divergentes                                                  */
/* ------------------------------------------------------------------ */

/** Score signé de chaque actif suivi, autour d'un axe central. */
export function BarresUnivers({
  items,
  seuil = 58,
  w = 420,
}: {
  items: PointUnivers[];
  seuil?: number;
  w?: number;
}) {
  if (!items.length) return null;
  const ligne = 17;
  const m: Marge = { t: 18, r: 10, b: 6, l: 78 };
  const h = m.t + items.length * ligne + m.b;
  const ampleur = Math.max(...items.map((i) => Math.abs(i.score)), seuil);
  const ech = niceScale(-ampleur, ampleur, 2);
  const sx = scaler(ech.min, ech.max, m.l, w - m.r);
  const zero = sx(0);

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Score signé des ${items.length} actifs suivis`}
    >
      {[-seuil, seuil].map((s) => (
        <g key={s}>
          <line
            className="c-grid"
            x1={Math.round(sx(s)) + 0.5}
            x2={Math.round(sx(s)) + 0.5}
            y1={m.t - 6}
            y2={h - m.b}
          />
          <text className="c-tick" x={sx(s)} y={m.t - 9} textAnchor="middle">
            {s > 0 ? "+" : ""}
            {s}
          </text>
        </g>
      ))}
      <line className="c-zero" x1={zero + 0.5} x2={zero + 0.5} y1={m.t - 6} y2={h - m.b} />

      {items.map((it, i) => {
        const y = m.t + i * ligne;
        const x = sx(it.score);
        const positif = it.score >= 0;
        const franchi = Math.abs(it.score) >= seuil;
        return (
          <g key={it.symbol}>
            <text className="c-tick" x={m.l - 8} y={y + ligne / 2 + 3} textAnchor="end">
              {it.symbol}
            </text>
            <rect
              className={`c-bar ${positif ? "up" : "down"}`}
              x={positif ? zero : x}
              y={y + 3}
              width={Math.max(1, Math.abs(x - zero))}
              height={ligne - 7}
              rx={1}
              opacity={franchi ? 1 : 0.4}
            >
              <title>
                {it.symbol} · score {num(it.score, 1)} · {it.regime.replace(/_/g, " ")}
              </title>
            </rect>
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Plan de trade                                                       */
/* ------------------------------------------------------------------ */

/**
 * Le plan en coupe : stop, entrée, objectif à l'échelle des prix.
 *
 * L'intérêt est de rendre le rapport risque/rendement visible plutôt que
 * lisible. Un R/R de 2,5 énoncé en chiffre ne dit rien à l'œil ; deux bandes
 * dont l'une fait deux fois et demie la hauteur de l'autre le disent d'un
 * coup, et un stop anormalement lointain devient impossible à manquer.
 */
export function PlanTrade({
  entry,
  stop,
  target,
  price,
  direction,
  w = 300,
  h = 88,
}: {
  entry: number;
  stop: number;
  target: number;
  price?: number;
  direction: "long" | "short";
  w?: number;
  h?: number;
}) {
  const valeurs = [entry, stop, target, ...(price ? [price] : [])];
  const [lo, hi] = extent(valeurs);
  const marge = (hi - lo) * 0.12 || 1;
  const m: Marge = { t: 10, r: 4, b: 10, l: 4 };
  const sx = scaler(lo - marge, hi + marge, m.l, w - m.r);
  const yHaut = m.t;
  const hauteur = h - m.t - m.b - 18;

  const bandes = [
    { a: stop, b: entry, cls: "down", label: "risque" },
    { a: entry, b: target, cls: "up", label: "gain visé" },
  ];

  const marque = (v: number, label: string, cls: string, dessus: boolean) => (
    <g key={label}>
      <line
        x1={Math.round(sx(v)) + 0.5}
        x2={Math.round(sx(v)) + 0.5}
        y1={yHaut - 3}
        y2={yHaut + hauteur + 3}
        style={{ stroke: `var(--${cls})`, strokeWidth: cls === "ink" ? 1.5 : 1 }}
      />
      <text
        className="c-tick"
        x={sx(v)}
        y={dessus ? yHaut - 5 : yHaut + hauteur + 13}
        textAnchor="middle"
        style={{ fill: `var(--${cls})` }}
      >
        {label}
      </text>
    </g>
  );

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Plan ${direction === "long" ? "à l'achat" : "à la vente"} : stop, entrée, objectif`}
      className="planchart"
    >
      {bandes.map((b) => {
        const x0 = Math.min(sx(b.a), sx(b.b));
        const x1 = Math.max(sx(b.a), sx(b.b));
        return (
          <rect
            key={b.label}
            x={x0}
            y={yHaut}
            width={Math.max(1, x1 - x0)}
            height={hauteur}
            rx={2}
            style={{ fill: `var(--${b.cls}-bg)`, stroke: `var(--${b.cls}-line)` }}
          >
            <title>{b.label}</title>
          </rect>
        );
      })}
      {marque(stop, "stop", "down", false)}
      {marque(target, "objectif", "up", false)}
      {marque(entry, "entrée", "ink", true)}
      {price !== undefined && Math.abs(price - entry) > (hi - lo) / 100 && (
        <circle className="c-dot" cx={sx(price)} cy={yHaut + hauteur / 2} r={2.5}>
          <title>dernier prix</title>
        </circle>
      )}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Barres comparées                                                    */
/* ------------------------------------------------------------------ */

/** Espérance par catégorie — régime, classe d'actif, issue. */
export function BarresCategories({
  items,
  w = 420,
  unite = "R",
}: {
  items: { label: string; valeur: number; n: number }[];
  w?: number;
  unite?: string;
}) {
  if (!items.length) return null;
  const ligne = 26;
  const m: Marge = { t: 8, r: 44, b: 8, l: 116 };
  const h = m.t + items.length * ligne + m.b;
  const ampleur = Math.max(...items.map((i) => Math.abs(i.valeur)), 0.01);
  const ech = niceScale(-ampleur, ampleur, 2);
  const sx = scaler(ech.min, ech.max, m.l, w - m.r);
  const zero = sx(0);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Espérance par catégorie en ${unite}`}>
      <line className="c-zero" x1={zero + 0.5} x2={zero + 0.5} y1={m.t} y2={h - m.b} />
      {items.map((it, i) => {
        const y = m.t + i * ligne;
        const x = sx(it.valeur);
        const positif = it.valeur >= 0;
        return (
          <g key={it.label}>
            <text className="c-tick" x={m.l - 8} y={y + ligne / 2 + 3} textAnchor="end">
              {it.label}
            </text>
            <rect
              className={`c-bar ${positif ? "up" : "down"}`}
              x={positif ? zero : x}
              y={y + 5}
              width={Math.max(1, Math.abs(x - zero))}
              height={ligne - 12}
              rx={1}
            />
            <text
              className="c-tick"
              x={w - m.r + 6}
              y={y + ligne / 2 + 3}
              style={{ fill: `var(--${positif ? "up" : "down"})` }}
            >
              {it.valeur >= 0 ? "+" : ""}
              {num(it.valeur, 2)}
            </text>
            <text className="c-label" x={m.l - 8} y={y + ligne / 2 + 13} textAnchor="end" opacity={0.7}>
              {it.n} trades
            </text>
          </g>
        );
      })}
    </svg>
  );
}
