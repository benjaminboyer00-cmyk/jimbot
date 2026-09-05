/**
 * Primitives de tracé.
 *
 * Aucune bibliothèque : les graphiques sont du SVG produit côté serveur,
 * envoyé dans le HTML, sans un octet de JavaScript côté client. Les couleurs
 * viennent des variables CSS, donc un changement de thème les redessine
 * sans rien recalculer.
 *
 * Le vocabulaire est volontairement minimal — une échelle, un chemin, des
 * graduations. Tout ce qui dépasse relève du composant qui l'appelle.
 */

export type Pt = { x: number; y: number };

/** Bornes d'une série, ramenées à un intervalle non dégénéré. */
export function extent(values: number[]): [number, number] {
  if (!values.length) return [0, 1];
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  // Une série plate n'a pas d'amplitude : sans marge, la courbe se colle à un
  // bord et la lecture devient fausse.
  if (lo === hi) return [lo - Math.abs(lo || 1) * 0.05, hi + Math.abs(hi || 1) * 0.05];
  return [lo, hi];
}

/**
 * Graduations « rondes ».
 *
 * Un axe gradué en 0,0347 / 0,0694 / 0,1041 est illisible : on cherche le pas
 * de la forme 1, 2, 5 × 10ⁿ le plus proche du pas idéal, puis on élargit le
 * domaine jusqu'aux multiples de ce pas.
 */
export function niceScale(
  min: number,
  max: number,
  count = 4,
): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    const c = Number.isFinite(min) ? min : 0;
    return { min: c - 1, max: c + 1, ticks: [c - 1, c, c + 1] };
  }
  const brut = (max - min) / Math.max(1, count);
  const magnitude = Math.pow(10, Math.floor(Math.log10(brut)));
  const norm = brut / magnitude;
  const pas = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * magnitude;
  const bas = Math.floor(min / pas) * pas;
  const haut = Math.ceil(max / pas) * pas;
  const ticks: number[] = [];
  // La comparaison porte sur une demi-graduation pour absorber l'erreur
  // d'accumulation des flottants sur les pas décimaux.
  for (let v = bas; v <= haut + pas / 2; v += pas) {
    ticks.push(Math.abs(v) < pas / 1e6 ? 0 : v);
  }
  return { min: bas, max: haut, ticks };
}

/** Interpolation linéaire domaine → pixels. */
export function scaler(
  d0: number,
  d1: number,
  r0: number,
  r1: number,
): (v: number) => number {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

const r2 = (n: number) => Math.round(n * 100) / 100;

/** Polyligne. Suffisant tant que les points sont plus serrés que le pixel. */
export function linePath(pts: Pt[]): string {
  if (!pts.length) return "";
  return pts.map((p, i) => `${i ? "L" : "M"}${r2(p.x)},${r2(p.y)}`).join(" ");
}

/**
 * Courbe lissée par splines cubiques monotones.
 *
 * Le lissage de Catmull-Rom classique dépasse les valeurs extrêmes : sur une
 * courbe de capital, il inventerait un creux qui n'a jamais eu lieu. Le
 * filtre de Fritsch-Carlson annule la tangente à chaque changement de sens,
 * ce qui garantit que la courbe ne sort jamais de l'enveloppe des points.
 */
export function smoothPath(pts: Pt[]): string {
  const n = pts.length;
  if (n < 3) return linePath(pts);

  const dx: number[] = [];
  const pente: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const h = pts[i + 1].x - pts[i].x;
    dx.push(h);
    pente.push(h === 0 ? 0 : (pts[i + 1].y - pts[i].y) / h);
  }

  const m: number[] = new Array(n);
  m[0] = pente[0];
  m[n - 1] = pente[n - 2];
  for (let i = 1; i < n - 1; i++) {
    // Extremum local : tangente nulle, donc pas de dépassement.
    m[i] = pente[i - 1] * pente[i] <= 0 ? 0 : (pente[i - 1] + pente[i]) / 2;
  }
  for (let i = 0; i < n - 1; i++) {
    if (pente[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / pente[i];
    const b = m[i + 1] / pente[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = (3 / Math.sqrt(s)) * pente[i];
      m[i] = t * a;
      m[i + 1] = t * b;
    }
  }

  let d = `M${r2(pts[0].x)},${r2(pts[0].y)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d +=
      ` C${r2(pts[i].x + h)},${r2(pts[i].y + m[i] * h)}` +
      ` ${r2(pts[i + 1].x - h)},${r2(pts[i + 1].y - m[i + 1] * h)}` +
      ` ${r2(pts[i + 1].x)},${r2(pts[i + 1].y)}`;
  }
  return d;
}

/** Aire sous une courbe, refermée sur une ligne de base. */
export function areaPath(pts: Pt[], base: number, smooth = false): string {
  if (!pts.length) return "";
  const haut = smooth ? smoothPath(pts) : linePath(pts);
  const fin = pts[pts.length - 1];
  return `${haut} L${r2(fin.x)},${r2(base)} L${r2(pts[0].x)},${r2(base)} Z`;
}

/** Longueur approchée d'une polyligne, pour l'animation de tracé. */
export function polyLength(pts: Pt[]): number {
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  }
  return Math.ceil(total);
}

/** Histogramme : bornes et effectifs. */
export function bins(values: number[], count: number): { x0: number; x1: number; n: number }[] {
  if (!values.length) return [];
  const [lo, hi] = extent(values);
  const largeur = (hi - lo) / count || 1;
  const paniers = Array.from({ length: count }, (_, i) => ({
    x0: lo + i * largeur,
    x1: lo + (i + 1) * largeur,
    n: 0,
  }));
  for (const v of values) {
    const i = Math.min(count - 1, Math.max(0, Math.floor((v - lo) / largeur)));
    paniers[i].n++;
  }
  return paniers;
}

/** Repli maximal depuis le plus haut atteint, en unités de la série. */
export function drawdown(values: number[]): number[] {
  let sommet = -Infinity;
  return values.map((v) => {
    if (v > sommet) sommet = v;
    return v - sommet;
  });
}

/** Identifiant stable pour les `<defs>` : deux graphiques sur la même page ne
 *  doivent pas partager un dégradé. */
export function gradId(prefix: string, seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return `${prefix}-${(h >>> 0).toString(36)}`;
}
