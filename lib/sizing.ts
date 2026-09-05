/**
 * Dimensionnement de position pour le capital du lecteur.
 *
 * Le moteur dimensionne déjà — mais pour son portefeuille papier, qui part de
 * 10 000 unités arbitraires. `/api/mt` transmet une fraction de risque et
 * laisse l'Expert Advisor en déduire son volume. Le site, lui, ne disait rien :
 * quelqu'un qui lit un plan d'entrée et de stop n'avait aucun moyen de savoir
 * combien en acheter, ce qui est pourtant la seule décision qui détermine ce
 * qu'il perd quand le plan échoue.
 *
 * Le raisonnement est celui du moteur, et il est inversé par rapport à
 * l'intuition : on ne décide pas « j'achète pour 1 000 € », on décide « je
 * perds au maximum 1 % si le stop saute », et la taille en découle de la
 * distance au stop.
 *
 * Les préréglages viennent du moteur, publiés dans l'instantané (`risque`).
 * Les recopier ici aurait créé deux règles de dimensionnement sur le même
 * site, qui auraient divergé au premier ajustement — sur le nombre qui décide
 * de combien on perd, c'est inacceptable.
 */
import type { Snapshot } from "./data";

export type ProfilRisque = {
  risque_pct: number;
  notionnel_max_pct: number;
  positions_max: number;
  stop_atr: number;
  rr_cible: number;
};

export type ReglagesRisque = {
  par_classe: Record<string, ProfilRisque>;
  risque_portefeuille_max: number;
  risque_correle_max: number;
};

/**
 * Repli pour les instantanés antérieurs à la publication des préréglages.
 *
 * Reprend `engine/jimbot/config.py` au moment de l'écriture. Il ne sert qu'à
 * afficher quelque chose de plausible sur un instantané ancien ; dès que le
 * moteur a rescanné, ce sont ses valeurs qui font foi.
 */
export const RISQUE_PAR_DEFAUT: ReglagesRisque = {
  par_classe: {
    crypto: { risque_pct: 0.01, notionnel_max_pct: 0.25, positions_max: 5, stop_atr: 2, rr_cible: 2 },
    meme: { risque_pct: 0.004, notionnel_max_pct: 0.05, positions_max: 3, stop_atr: 2.5, rr_cible: 3 },
    forex: { risque_pct: 0.008, notionnel_max_pct: 0.6, positions_max: 4, stop_atr: 1.8, rr_cible: 2 },
    index: { risque_pct: 0.008, notionnel_max_pct: 0.5, positions_max: 3, stop_atr: 2.2, rr_cible: 2 },
  },
  risque_portefeuille_max: 0.06,
  risque_correle_max: 0.035,
};

export const reglagesRisque = (snap?: Snapshot | null): ReglagesRisque =>
  snap?.risque ?? RISQUE_PAR_DEFAUT;

export const profil = (r: ReglagesRisque, klass: string): ProfilRisque =>
  r.par_classe[klass] ?? r.par_classe.crypto ?? RISQUE_PAR_DEFAUT.par_classe.crypto;

export type Taille = {
  /** Quantité de l'instrument. */
  unites: number;
  /** Valeur de la position à l'entrée. */
  notionnel: number;
  /** Perte si le stop est touché, dans la monnaie du compte. */
  perte_au_stop: number;
  /** Cette perte, en pourcentage du capital. */
  perte_pct: number;
  /** Distance au stop, en pourcentage du prix d'entrée. */
  distance_pct: number;
  /** Facteur de conviction appliqué au risque nominal. */
  conviction: number;
  /** Le plafond d'exposition de la classe a-t-il mordu ? */
  plafonne: boolean;
  /** Fraction du capital engagée par le notionnel. */
  exposition_pct: number;
};

/**
 * Modulation du risque par la conviction.
 *
 * Reprend `risk.position_size` : un score de 50 engage un dixième du risque
 * nominal, un score de 100 l'engage entièrement. Elle est conservée ici parce
 * qu'un lecteur qui dimensionne autrement que le moteur ne dimensionnerait
 * plus la même stratégie.
 */
export const conviction = (score: number): number =>
  Math.min(1, Math.max(0.1, (score - 50) / 50));

/**
 * Taille d'une position.
 *
 * `risqueNominalPct` est exprimé en pourcentage (1 pour 1 %) : c'est ce que le
 * lecteur saisit. Il remplace le préréglage de classe du moteur — c'est son
 * compte, donc sa décision — mais la modulation par la conviction et le
 * plafond d'exposition de la classe continuent de s'appliquer, faute de quoi
 * ce ne serait plus le même système.
 */
export function taillePosition(
  capital: number,
  entree: number,
  stop: number,
  klass: string,
  score: number,
  risqueNominalPct: number,
  reglages: ReglagesRisque,
): Taille | null {
  const parUnite = Math.abs(entree - stop);
  if (!(parUnite > 0) || !(entree > 0) || !(capital > 0) || !(risqueNominalPct > 0)) {
    return null;
  }

  const p = profil(reglages, klass);
  const conv = conviction(score);
  const risquePct = (risqueNominalPct / 100) * conv;

  let perte = capital * risquePct;
  let unites = perte / parUnite;
  let notionnel = unites * entree;

  // Un stop très serré produit une taille énorme : on plafonne le notionnel,
  // quitte à risquer moins que demandé. C'est la protection contre le gap —
  // le saut de prix qui traverse le stop sans l'exécuter.
  const plafond = capital * p.notionnel_max_pct;
  const plafonne = notionnel > plafond;
  if (plafonne) {
    unites = plafond / entree;
    notionnel = plafond;
    perte = unites * parUnite;
  }

  return {
    unites,
    notionnel,
    perte_au_stop: perte,
    perte_pct: (perte / capital) * 100,
    distance_pct: (parUnite / entree) * 100,
    conviction: conv,
    plafonne,
    exposition_pct: (notionnel / capital) * 100,
  };
}
