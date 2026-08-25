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

async function readRemote<T>(name: string): Promise<T | null> {
  if (!REMOTE_BASE) return null;
  try {
    const res = await fetch(`${REMOTE_BASE}/${name}.json`, { cache: "no-store" });
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
