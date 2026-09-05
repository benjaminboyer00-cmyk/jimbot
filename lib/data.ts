/**
 * Lecture des fichiers produits par le moteur Python.
 *
 * Le moteur committe ses résultats dans `data/`, ce qui déclenche un
 * redéploiement Vercel : le dashboard est donc toujours servi avec les
 * données du dernier scan, sans base de données ni API intermédiaire.
 */
import fs from "node:fs/promises";
import path from "node:path";

export type Factor = {
  name: string;
  value: number;
  weight: number;
  detail: string;
  contribution: number;
};

export type Signal = {
  symbol: string;
  label: string;
  klass: string;
  direction: "long" | "short" | "neutre";
  score: number;
  raw_score: number;
  price: number;
  regime: { name: string; quality: number; hurst: number; vol_percentile: number };
  factors: Factor[];
  entry: number;
  stop: number;
  target: number;
  rr: number;
  atr_pct: number;
  timeframe: string;
  news_score: number;
  news_count: number;
  warnings: string[];
  win_prob: number;
  expected_r: number;
  stop_basis: string;
  target_basis: string;
  bias: "long" | "short" | "neutre";
  actionable: boolean;
};

export type Speech = {
  title: string;
  source: string;
  url: string;
  speaker: string;
  tone: number;
  terms: string[];
  importance: number;
  impact: Record<string, number>;
};

export type Report = {
  name: string;
  date: string;
  size_kb: number;
  path: string;
};

export type RiskOff = {
  level: number;
  count: number;
  top: { title: string; source: string; risk: number; terms: string[]; url: string }[];
};

export type Position = {
  symbol: string;
  label: string;
  direction: string;
  entry: number;
  stop: number;
  target: number;
  risk_amount: number;
  bars_held: number;
  stop_note: string;
  mfe: number;
  mae: number;
};

export type Trade = {
  symbol: string;
  label: string;
  direction: string;
  entry: number;
  exit: number;
  pnl: number;
  pnl_pct: number;
  r_multiple: number;
  reason: string;
  closed_at: string;
  regime: string;
};

export type Article = {
  title: string;
  source: string;
  url: string;
  sentiment: number;
  age_hours: number;
  assets: string[];
  category?: "marches" | "monde";
  risk?: number;
  risk_terms?: string[];
};

export type Memecoin = {
  symbol: string;
  name: string;
  chain: string;
  price_usd: number;
  liquidity_usd: number;
  volume_24h: number;
  change_24h: number;
  age_hours: number;
  health_score: number;
  url: string;
};

export type Snapshot = {
  generated_at: string;
  signals: Signal[];
  regimes: Record<string, number>;
  memecoins: Memecoin[];
  meme_report: { screened?: number; retained?: number; near_misses?: unknown[] };
  risk_off?: RiskOff;
  news_summary?: string;
  news_engine?: string;
  speeches?: Speech[];
  watchlist?: Signal[];
  agenda?: {
    mechanical: {
      date: string;
      days_ahead: number;
      label: string;
      impact: string;
      detail: string;
    }[];
    press: { label: string; impact: string; detail: string; source: string; url: string }[];
    high_impact: number;
  };
  reports?: Report[];
  news: Article[];
  portfolio: {
    capital: number;
    initial: number;
    equity: number;
    open_risk: number;
    positions: Position[];
    equity_curve: { t: string; equity: number }[];
    closed_count: number;
  };
  counts: { analysed: number; actionable: number; long: number; short: number };
};

export type Performance = {
  trades: number;
  win_rate?: number;
  total_pnl?: number;
  total_return_pct?: number;
  profit_factor?: number | null;
  expectancy_r?: number;
  max_drawdown_pct?: number;
  sharpe?: number;
  total_fees?: number;
  max_loss_streak?: number;
};

/* ------------------------------------------------------------------ */
/* Validation et sondage de facteurs                                   */
/*                                                                     */
/* Ces deux formes vivaient dans `app/sections.tsx`, ce qui obligeait   */
/* `lib/` à importer depuis `app/` pour se typer lui-même. Elles sont   */
/* remontées ici : la couche de données décrit ses propres fichiers.    */
/* ------------------------------------------------------------------ */

export type BacktestTrade = {
  symbol: string;
  klass: string;
  direction: "long" | "short";
  score: number;
  win_prob: number;
  expected_r: number;
  rr: number;
  regime: string;
  entry: number;
  stop: number;
  target: number;
  exit: number;
  outcome: "cible" | "stop" | "expiration" | string;
  r_multiple: number;
  bars_held: number;
  mfe: number;
  mae: number;
  stop_atr: number;
  stop_basis: string;
  target_basis: string;
  stop_strength: number;
  obstacle: number;
  index: number;
};

export type Backtest = {
  generated_at: string;
  parametres: { bars: number; step: number; window: number; actifs: number };
  calibration: {
    trades: number;
    win_rate_global: number;
    prob_predite_moyenne: number;
    esperance_realisee: number;
    esperance_predite: number;
    facteur_de_profit: number | null;
    drawdown_max_R?: number;
    ic95?: [number, number];
    verdict?: string;
    significatif?: boolean;
    trades_necessaires?: number;
    correlation_score_esperance?: number;
    par_tranche_de_score?: {
      tranche: string;
      trades: number;
      win_rate: number;
      prob_predite: number;
      esperance_realisee: number;
      esperance_predite?: number;
    }[];
    par_regime?: Record<string, { trades: number; esperance: number; win_rate: number }>;
    par_classe?: Record<string, { trades: number; esperance: number; win_rate: number }>;
    par_issue?: Record<string, { trades: number; esperance: number; win_rate: number }>;
  };
  effet_de_la_structure?: {
    adosse_a_la_structure?: {
      trades: number;
      win_rate: number;
      esperance: number;
      rr_moyen: number;
      /** Taux de réussite en deçà duquel le R/R moyen ne suffit plus. */
      seuil_rentabilite: number;
      ecart_au_seuil: number;
    };
  };
  effet_des_penalites?: {
    par_distance_de_stop_atr?: {
      tranche: string;
      trades: number;
      win_rate: number;
      seuil_rentabilite: number;
      ecart_au_seuil: number;
      esperance: number;
      prob_predite: number;
    }[];
  };
  limites: string[];
  trades?: BacktestTrade[];
};

export type ProbeHorizon = { ic: number; t: number; n?: number; significatif: boolean };

export type Probe = {
  generated_at: string;
  parametres: { bars: number; step: number; actifs: number; horizons?: string[] };
  coefficients: {
    observations: number;
    horizons?: string[];
    par_facteur: Record<
      string,
      {
        ic_max: number;
        meilleur_horizon: string;
        significatif: boolean;
        horizons: Record<string, ProbeHorizon>;
      }
    >;
  };
  note: string;
};

const DATA_DIR = path.join(process.cwd(), "data");

/**
 * Base distante des fichiers de données.
 *
 * Le moteur committe `data/*.json` toutes les 15 minutes. Si le dashboard
 * lisait ces fichiers depuis son propre bundle, il faudrait un redéploiement
 * à chaque scan — soit 96 déploiements par jour, alors que le plan Hobby de
 * Vercel en autorise 100. On lirait aussi des données figées à l'instant du
 * build.
 *
 * On lit donc directement le dépôt à chaque requête : le dashboard est
 * toujours à jour et le déploiement ne bouge que lorsque le code change.
 */
const REMOTE_BASE =
  process.env.JIMBOT_DATA_URL ??
  "https://raw.githubusercontent.com/benjaminboyer00-cmyk/jimbot/main/data";

async function readLocal<T>(name: string): Promise<T | null> {
  try {
    const raw = await fs.readFile(path.join(DATA_DIR, `${name}.json`), "utf8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Durée de mise en cache d'une lecture distante.
 *
 * `no-store` semblait la lecture la plus honnête — toujours la donnée la plus
 * fraîche. En pratique elle ne gagnait rien et coûtait cher : les scans sont
 * espacés de plusieurs heures, donc une réponse vieille d'une minute est
 * strictement la même. Chaque page vue déclenchait en revanche quatre appels
 * à `raw.githubusercontent`, non authentifiés, depuis des adresses Vercel
 * partagées et soumises à quota. Sous la moindre affluence, le site ne
 * servait plus rien du tout.
 *
 * Soixante secondes suffisent à absorber une rafale de visiteurs sans qu'un
 * lecteur puisse jamais voir un scan qu'il aurait manqué.
 */
const CACHE_SECONDES = 60;

async function readRemote<T>(name: string): Promise<T | null> {
  if (!REMOTE_BASE) return null;
  try {
    const res = await fetch(`${REMOTE_BASE}/${name}.json`, {
      next: { revalidate: CACHE_SECONDES },
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

async function readJson<T>(name: string, fallback: T): Promise<T> {
  // En développement, le fichier local fait foi : sinon on travaillerait sur
  // les données du dépôt et un scan lancé en local resterait invisible.
  // En production, c'est l'inverse : le dépôt est la source, et le fichier
  // embarqué dans le bundle est figé à l'instant du build.
  const order =
    process.env.NODE_ENV === "production"
      ? [readRemote<T>, readLocal<T>]
      : [readLocal<T>, readRemote<T>];

  for (const read of order) {
    const value = await read(name);
    if (value !== null) return value;
  }
  // Fichier absent avant le premier scan : le dashboard doit s'afficher
  // malgré tout, avec un état vide explicite.
  return fallback;
}

export const getSnapshot = () => readJson<Snapshot | null>("latest", null);
export const getTrades = () => readJson<Trade[]>("trades", []);
export const getProbe = () => readJson<Probe | null>("probe", null);
export const getBacktest = () => readJson<Backtest | null>("backtest", null);
export const getLastReport = () =>
  readJson<{ path: string; generated_at: string; briefing: string; engine: string } | null>(
    "last_report",
    null,
  );

/** Formatage adapté à l'ordre de grandeur : un memecoin et un indice ne se
 *  formatent pas avec le même nombre de décimales. */
export function fmtPrice(v: number): string {
  const digits = v >= 1000 ? 2 : v >= 1 ? 4 : v >= 0.01 ? 6 : 10;
  return v.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtNum(v: number, digits = 2): string {
  return v.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtCompact(v: number): string {
  return v.toLocaleString("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
}

export function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 60000;
  if (!Number.isFinite(diff)) return "—";
  if (diff < 1) return "à l'instant";
  if (diff < 60) return `il y a ${Math.round(diff)} min`;
  if (diff < 1440) return `il y a ${Math.round(diff / 60)} h`;
  return `il y a ${Math.round(diff / 1440)} j`;
}

export const REGIME_LABELS: Record<string, string> = {
  tendance_haussière: "tendance haussière",
  tendance_baissière: "tendance baissière",
  range: "range",
  chaotique: "chaotique",
};
