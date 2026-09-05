/**
 * Dérivation des séries.
 *
 * Le moteur écrit des états et des listes de trades ; un graphique demande
 * des séries. Cette couche fait la traduction, une fois, au même endroit —
 * de sorte que la page, la page « Courbes » et l'API `/api/curves` montrent
 * exactement les mêmes nombres. Un graphique qui contredirait le tableau
 * au-dessus de lui serait pire qu'aucun graphique.
 */
import {
  drawdown,
  type Pt,
} from "./chart";
import type { Backtest, BacktestTrade, Probe, Snapshot, Trade } from "./data";

export type SeriePoint = { i: number; t?: string; v: number };

/* ------------------------------------------------------------------ */
/* Capital du portefeuille papier                                      */
/* ------------------------------------------------------------------ */

export type EquiteSerie = {
  points: SeriePoint[];
  repli: SeriePoint[];
  initial: number;
  final: number;
  sommet: number;
  repli_max: number;
  debut?: string;
  fin?: string;
};

export function serieEquite(p: Snapshot["portfolio"]): EquiteSerie {
  const brut = p?.equity_curve ?? [];
  const points: SeriePoint[] = brut.map((e, i) => ({ i, t: e.t, v: e.equity }));
  const valeurs = points.map((x) => x.v);
  const dd = drawdown(valeurs);
  const initial = p?.initial ?? valeurs[0] ?? 0;
  return {
    points,
    repli: dd.map((v, i) => ({ i, t: points[i]?.t, v })),
    initial,
    final: valeurs.length ? valeurs[valeurs.length - 1] : initial,
    sommet: valeurs.length ? Math.max(...valeurs) : initial,
    repli_max: dd.length ? Math.min(...dd) : 0,
    debut: points[0]?.t,
    fin: points[points.length - 1]?.t,
  };
}

/* ------------------------------------------------------------------ */
/* Cumul de R                                                          */
/* ------------------------------------------------------------------ */

/**
 * Courbe de R cumulés.
 *
 * L'axe est le numéro de trade, pas la date : c'est la mesure honnête d'une
 * stratégie. Une période sans trade n'est ni un gain ni une perte, et
 * l'étaler sur le temps donnerait à un mois creux l'allure d'un plateau
 * maîtrisé.
 */
export type CumulSerie = {
  points: SeriePoint[];
  repli: SeriePoint[];
  total: number;
  repli_max: number;
  gagnants: number;
  perdants: number;
};

export function serieCumulR(rs: number[], dates: (string | undefined)[] = []): CumulSerie {
  const points: SeriePoint[] = [{ i: 0, v: 0 }];
  let cumul = 0;
  rs.forEach((r, i) => {
    cumul += r;
    points.push({ i: i + 1, t: dates[i], v: Math.round(cumul * 1000) / 1000 });
  });
  const dd = drawdown(points.map((x) => x.v));
  return {
    points,
    repli: dd.map((v, i) => ({ i, t: points[i]?.t, v })),
    total: Math.round(cumul * 1000) / 1000,
    repli_max: dd.length ? Math.min(...dd) : 0,
    gagnants: rs.filter((r) => r > 0).length,
    perdants: rs.filter((r) => r <= 0).length,
  };
}

export const cumulTradesPapier = (trades: Trade[]): CumulSerie =>
  serieCumulR(
    trades.map((t) => t.r_multiple),
    trades.map((t) => t.closed_at),
  );

export function cumulBacktest(bt?: Backtest | null): CumulSerie | null {
  const trades = bt?.trades;
  if (!trades?.length) return null;
  // Les trades sont écrits dans l'ordre de découverte : on les remet dans
  // l'ordre chronologique de la fenêtre rejouée avant de cumuler, sinon la
  // courbe décrit une séquence qui n'a jamais existé.
  const ordre = [...trades].sort((a, b) => a.index - b.index);
  return serieCumulR(ordre.map((t) => t.r_multiple));
}

/* ------------------------------------------------------------------ */
/* Distribution des R                                                  */
/* ------------------------------------------------------------------ */

export type Panier = { x0: number; x1: number; n: number };

/**
 * Distribution des R.
 *
 * Les paniers sont alignés sur zéro plutôt que sur le minimum observé : sur
 * une stratégie à stop fixe, la frontière entre perte et gain est le fait
 * saillant, et un panier à cheval sur zéro la rendrait invisible.
 */
export function distributionR(rs: number[], largeur = 0.5): Panier[] {
  if (!rs.length) return [];
  const lo = Math.floor(Math.min(...rs) / largeur) * largeur;
  const hi = Math.ceil(Math.max(...rs) / largeur) * largeur;
  const n = Math.max(1, Math.round((hi - lo) / largeur));
  const paniers: Panier[] = Array.from({ length: n }, (_, i) => ({
    x0: lo + i * largeur,
    x1: lo + (i + 1) * largeur,
    n: 0,
  }));
  for (const r of rs) {
    const i = Math.min(n - 1, Math.max(0, Math.floor((r - lo) / largeur)));
    paniers[i].n++;
  }
  return paniers;
}

/* ------------------------------------------------------------------ */
/* Excursions                                                          */
/* ------------------------------------------------------------------ */

export type PointExcursion = {
  mfe: number;
  mae: number;
  r: number;
  issue: string;
  symbol: string;
};

/**
 * MFE contre MAE.
 *
 * Chaque trade devient un point : jusqu'où il est allé dans le bon sens, et
 * jusqu'où il est descendu avant. C'est le seul graphique qui dise si les
 * pertes ont été des erreurs de sens ou des stops trop serrés — un nuage de
 * perdants à fort MFE signifie que le marché est allé chercher l'objectif
 * sans qu'on soit resté dedans.
 */
export function excursions(bt?: Backtest | null): PointExcursion[] {
  return (bt?.trades ?? []).map((t: BacktestTrade) => ({
    mfe: t.mfe,
    mae: t.mae,
    r: t.r_multiple,
    issue: t.outcome,
    symbol: t.symbol,
  }));
}

/* ------------------------------------------------------------------ */
/* Calibration                                                         */
/* ------------------------------------------------------------------ */

export type PointCalibration = {
  tranche: string;
  trades: number;
  predite: number;
  observee: number;
  esperance: number;
};

/** Réussite prédite contre réussite observée, par tranche de score. */
export function calibration(bt?: Backtest | null): PointCalibration[] {
  return (bt?.calibration?.par_tranche_de_score ?? []).map((t) => ({
    tranche: t.tranche,
    trades: t.trades,
    predite: t.prob_predite,
    observee: t.win_rate,
    esperance: t.esperance_realisee,
  }));
}

/* ------------------------------------------------------------------ */
/* Pouvoir prédictif des facteurs                                      */
/* ------------------------------------------------------------------ */

export type CourbeIC = {
  facteur: string;
  ic_max: number;
  significatif: boolean;
  points: { horizon: string; bougies: number; ic: number; t: number; significatif: boolean }[];
};

/**
 * IC par horizon, prêt à tracer.
 *
 * Les horizons sont nommés `h6`, `h12`… : on en extrait le nombre de bougies
 * pour que l'axe soit une vraie échelle et non une suite d'étiquettes
 * équidistantes. Un IC qui croît de 6 à 48 bougies raconte autre chose qu'un
 * IC plat, et l'espacement doit le montrer.
 */
export function courbesIC(probe?: Probe | null): CourbeIC[] {
  const par = probe?.coefficients?.par_facteur;
  if (!par) return [];
  return Object.entries(par)
    .map(([facteur, v]) => ({
      facteur,
      ic_max: v.ic_max,
      significatif: v.significatif,
      points: Object.entries(v.horizons)
        .map(([horizon, h]) => ({
          horizon,
          bougies: Number(horizon.replace(/\D/g, "")) || 0,
          ic: h.ic,
          t: h.t,
          significatif: h.significatif,
        }))
        .sort((a, b) => a.bougies - b.bougies),
    }))
    .sort((a, b) => Math.abs(b.ic_max) - Math.abs(a.ic_max));
}

/* ------------------------------------------------------------------ */
/* Univers                                                             */
/* ------------------------------------------------------------------ */

export type PointUnivers = { symbol: string; score: number; klass: string; regime: string };

/** Score signé de chaque actif suivi, trié — la photo du marché du jour. */
export function universSigne(snap: Snapshot): PointUnivers[] {
  return snap.signals
    .map((s) => ({
      symbol: s.symbol,
      score:
        s.direction === "short"
          ? -s.score
          : s.direction === "long"
            ? s.score
            : s.raw_score >= 0
              ? s.score
              : -s.score,
      klass: s.klass,
      regime: s.regime.name,
    }))
    .sort((a, b) => b.score - a.score);
}

/* ------------------------------------------------------------------ */
/* Conversion en pixels                                                */
/* ------------------------------------------------------------------ */

/** Projette une série sur une boîte de dessin. */
export function projeter(
  points: SeriePoint[],
  sx: (v: number) => number,
  sy: (v: number) => number,
): Pt[] {
  return points.map((p) => ({ x: sx(p.i), y: sy(p.v) }));
}

/* ------------------------------------------------------------------ */
/* Fiabilité mesurée                                                   */
/* ------------------------------------------------------------------ */

export type BandeMesuree = {
  tranche: string;
  trades: number;
  win_rate: number;
  esperance: number;
  /** Écart entre ce que le modèle annonçait et ce qui est arrivé, en points. */
  ecart_a_la_prediction: number;
};

/**
 * Ce que la tranche de score d'un signal a réellement produit.
 *
 * Une carte de signal affiche une probabilité de gain et une espérance : ce
 * sont deux affirmations du modèle sur lui-même. Elles ne disent rien de ce
 * qui est arrivé aux trades qui portaient le même score. Pour quelqu'un qui
 * s'appuie sur le site pour passer un ordre, c'est la seconde information
 * qui compte, et c'est celle qui manquait.
 */
export function bandeDuScore(
  bt: Backtest | null | undefined,
  score: number,
): BandeMesuree | null {
  const tranches = bt?.calibration?.par_tranche_de_score;
  if (!tranches?.length) return null;
  for (const t of tranches) {
    const [lo, hi] = t.tranche.split("-").map(Number);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) continue;
    if (score >= lo && score < hi) {
      return {
        tranche: t.tranche,
        trades: t.trades,
        win_rate: t.win_rate,
        esperance: t.esperance_realisee,
        ecart_a_la_prediction: t.win_rate - t.prob_predite,
      };
    }
  }
  return null;
}

/** Corrélation de rang de Spearman, avec gestion des ex æquo. */
function spearman(xs: number[], ys: number[]): number {
  const n = xs.length;
  if (n < 3) return 0;
  const rangs = (v: number[]): number[] => {
    const ordre = v.map((_, i) => i).sort((a, b) => v[a] - v[b]);
    const r = new Array<number>(n);
    let i = 0;
    while (i < n) {
      let j = i;
      while (j + 1 < n && v[ordre[j + 1]] === v[ordre[i]]) j++;
      const moyen = (i + j) / 2 + 1;
      for (let k = i; k <= j; k++) r[ordre[k]] = moyen;
      i = j + 1;
    }
    return r;
  };
  const rx = rangs(xs);
  const ry = rangs(ys);
  const mx = rx.reduce((a, b) => a + b, 0) / n;
  const my = ry.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let dx = 0;
  let dy = 0;
  for (let i = 0; i < n; i++) {
    num += (rx[i] - mx) * (ry[i] - my);
    dx += (rx[i] - mx) ** 2;
    dy += (ry[i] - my) ** 2;
  }
  const den = Math.sqrt(dx * dy);
  return den ? num / den : 0;
}

export type Discrimination = {
  rho: number;
  n: number;
  t: number;
  significatif: boolean;
  /** Demi-largeur de l'intervalle de confiance à 95 %. */
  marge: number;
};

/**
 * Le score discrimine-t-il, mesuré trade par trade.
 *
 * Le moteur publie une corrélation calculée sur les *tranches* agrégées.
 * Quand il n'y a que deux tranches — le cas courant — un Spearman vaut
 * toujours exactement ±1 : le nombre a l'air d'une mesure précise alors
 * qu'il ne peut prendre que deux valeurs. On recalcule donc sur les trades
 * individuels, où le coefficient a un sens et un intervalle.
 */
export function discrimination(bt?: Backtest | null): Discrimination | null {
  const trades = bt?.trades;
  if (!trades || trades.length < 10) return null;
  const rho = spearman(
    trades.map((t) => t.score),
    trades.map((t) => t.r_multiple),
  );
  const n = trades.length;
  const se = 1 / Math.sqrt(n - 1);
  return {
    rho,
    n,
    t: rho / se,
    significatif: Math.abs(rho / se) > 2,
    marge: 1.96 * se,
  };
}
