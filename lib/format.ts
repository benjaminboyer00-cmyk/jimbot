/**
 * Formatage et libellés.
 *
 * Séparé de `lib/data.ts`, qui importe `node:fs` pour lire les fichiers du
 * moteur : un composant client qui voulait juste formater un prix entraînait
 * tout le module de lecture dans le paquet du navigateur, et la compilation
 * échouait. Ces fonctions ne dépendent de rien.
 */

/**
 * Séparateur de milliers lisible.
 *
 * `toLocaleString("fr-FR")` sépare les milliers par une espace fine
 * insécable (U+202F). En chasse fixe elle occupe une cellule entière et se
 * voit ; dans l'Instrument Sans elle mesure moins d'un pixel, et « 10 024 »
 * s'affiche « 10024 » partout où un montant sort d'un tableau. L'espace
 * insécable ordinaire tient le même rôle typographique, se voit dans les deux
 * familles, et reste insécable — un montant ne se coupera jamais en fin de
 * ligne.
 */
function separateurLisible(s: string): string {
  return s.replace(/\u202f/g, "\u00a0");
}

/** Formatage adapté à l'ordre de grandeur : un memecoin et un indice ne se
 *  formatent pas avec le même nombre de décimales. */
export function fmtPrice(v: number): string {
  const digits = v >= 1000 ? 2 : v >= 1 ? 4 : v >= 0.01 ? 6 : 10;
  return separateurLisible(
    v.toLocaleString("fr-FR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }),
  );
}

export function fmtNum(v: number, digits = 2): string {
  return separateurLisible(
    v.toLocaleString("fr-FR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }),
  );
}

export function fmtCompact(v: number): string {
  return separateurLisible(
    v.toLocaleString("fr-FR", { notation: "compact", maximumFractionDigits: 1 }),
  );
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
