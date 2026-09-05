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
  /**
   * Ce que l'actif fait en ce moment. Mesuré par le moteur, sans aucun modèle
   * ni prédiction — voir `mouvement()` dans `engine/jimbot/strategy.py`.
   *
   * Optionnel : les instantanés antérieurs à son introduction n'en ont pas, et
   * un actif dont l'historique ne couvre pas une semaine renvoie
   * `disponible: false` plutôt qu'une intensité inventée sur trois jours.
   */
  mouvement?: Mouvement;
};

export type Mouvement =
  | { disponible: false }
  | {
      disponible: true;
      /** Variation nette, en pourcentage. `null` si l'historique est trop court. */
      var_1h: number | null;
      var_24h: number;
      var_7j: number | null;
      /** Distance réellement parcourue sur 24 h (haut − bas), en pourcentage. */
      amplitude_pct: number;
      /** Ce que parcourt cet actif un jour ordinaire. `null` faute d'étalon. */
      amplitude_ref_pct: number | null;
      /** Parcours du jour rapporté à cette journée ordinaire. 2 = deux fois. */
      ampleur: number;
      /** Part du parcours conservée : 1 finit sur un extrême, 0 revient au départ. */
      retention: number;
      /** Position dans l'amplitude des 24 h : 0 sur le plus bas, 1 sur le plus haut. */
      position_range: number;
      /** Volume de la dernière bougie rapporté à sa médiane sur 48 h. */
      volume_rel: number;
      etat: string;
      /** Parcours ample dont il ne reste presque rien. */
      rendu: boolean;
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

/**
 * Seuils de décision en vigueur.
 *
 * Ils sont réglables par variable d'environnement côté moteur. Le site les
 * affichait en dur, si bien qu'un changement de seuil aurait laissé la page
 * annoncer l'ancien — sur un site dont des gens tirent des ordres, c'est le
 * genre d'écart qui coûte cher. Le scan les publie donc dans son instantané.
 */
export type Seuils = { signal: number; alerte: number; ping: number };

/** Valeurs de repli, pour les instantanés antérieurs à leur publication. */
export const SEUILS_PAR_DEFAUT: Seuils = { signal: 58, alerte: 68, ping: 80 };

export const seuils = (snap?: Snapshot | null): Seuils =>
  snap?.seuils ?? SEUILS_PAR_DEFAUT;

export type Snapshot = {
  generated_at: string;
  seuils?: Seuils;
  /** Préréglages de risque du moteur. Voir `lib/sizing.ts`. */
  risque?: import("./sizing").ReglagesRisque;
  signals: Signal[];
  /** Table d alias des instruments MetaTrader, publiee par le moteur. */
  mt_aliases?: Record<string, string[]>;
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

/* ------------------------------------------------------------------ */
/* Mémoire : la trajectoire de chaque actif, scan après scan           */
/* ------------------------------------------------------------------ */

/**
 * Un point d'historique, tel qu'il est stocké.
 *
 * Le format est colonnaire et non nommé — `["2026-09-05T07:20:00+00:00",
 * 4358.1, 58.1, 2, 1]`. Le fichier est réécrit et committé à chaque scan :
 * répéter cinq noms de champs sur des milliers de lignes coûterait plus cher
 * que toutes les données réunies. Le tuple est déballé une seule fois, ici,
 * par `serieActif`.
 */
export type PointHistorique = [
  /** horodatage du scan */ string,
  /** prix */ number,
  /** score **signé** : positif à l'achat, négatif à la vente */ number,
  /** indice dans `History.regimes` */ number,
  /** 1 si un signal a été émis à ce passage */ number,
];

export type History = {
  generated_at: string | null;
  champs: string[];
  /** Légende des régimes. L'ordre est figé : les points stockent un indice. */
  regimes: string[];
  points_max: number;
  actifs: Record<
    string,
    { label: string; klass: string; points: PointHistorique[] }
  >;
};

/* ------------------------------------------------------------------ */
/* Redevabilité : ce qu'ont donné les signaux réellement émis          */
/* ------------------------------------------------------------------ */

/**
 * Issue d'un signal.
 *
 * - `cible` / `stop` / `expiration` : le marché a tranché ;
 * - `en_cours` : le trade court encore ;
 * - `hors_portee` : le signal précède la fenêtre de bougies dont dispose le
 *   moteur, son issue ne sera jamais connue ;
 * - `indetermine` : les données manquaient au moment de la mesure.
 */
export type Issue =
  | "cible"
  | "stop"
  | "expiration"
  | "en_cours"
  | "hors_portee"
  | "indetermine";

export type SignalSuivi = {
  id: string;
  symbol: string;
  label: string;
  klass: string;
  direction: "long" | "short";
  premiere_emission: string;
  derniere_emission: string;
  /** Nombre de fois où le moteur a réémis ce même signal. */
  emissions: number;
  score: number;
  score_max: number;
  regime: string;
  entry: number;
  stop: number;
  target: number;
  rr: number;
  /** Le score a-t-il franchi le seuil de publication Discord ? */
  alerte_discord: boolean;
  issue: Issue;
  resolu_le: string | null;
  prix_sortie: number | null;
  r_multiple: number | null;
  bougies: number;
  mfe: number;
  mae: number;
  dernier_prix: number | null;
  r_courant: number | null;
  mesure_le: string;
};

export type Suivi = {
  generated_at: string;
  horizon_bougies: number;
  seuil_alerte: number;
  fenetre_regroupement_min: number;
  methode: string;
  resume: {
    emissions: number;
    signaux: number;
    publies_discord: number;
    par_issue: Record<string, number>;
    tranches: number;
    win_rate: number | null;
    esperance_r: number | null;
    total_r: number | null;
    significatif: boolean;
    /** Bilan coupé à la bascule de construction des plans. Optionnel : les
     *  instantanés antérieurs à son introduction ne le portent pas. */
    par_version?: BilanParVersion;
  };
  signaux: SignalSuivi[];
};

export type BilanVersion = {
  signaux: number;
  tranches: number;
  win_rate: number | null;
  esperance_r: number | null;
  total_r: number | null;
  significatif: boolean;
};

export type BilanParVersion = {
  bascule: string;
  optimiseur_de_niveaux: BilanVersion;
  plan_fixe: BilanVersion;
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
export const getHistory = () => readJson<History | null>("history", null);
export const getSuivi = () => readJson<Suivi | null>("suivi", null);
export const getLastReport = () =>
  readJson<{ path: string; generated_at: string; briefing: string; engine: string } | null>(
    "last_report",
    null,
  );

/* Le formatage et les libellés vivent dans `lib/format.ts` : ils ne lisent
   aucun fichier et doivent rester utilisables par un composant client, ce que
   ce module — qui importe `node:fs` — interdit. Ils sont ré-exposés ici pour
   que les appelants n'aient pas à savoir où ils sont passés. */
export { fmtPrice, fmtNum, fmtCompact, timeAgo, REGIME_LABELS } from "./format";
