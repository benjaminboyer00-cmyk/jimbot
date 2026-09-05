/**
 * `GET /api/curves` — service de courbes.
 *
 * Deux formats pour la même donnée :
 *
 * - `format=json` (défaut) renvoie les séries déjà dérivées — courbe de
 *   capital, R cumulés, distribution, calibration, coefficients
 *   d'information. Un tableur ou un carnet Python les trace directement,
 *   sans reproduire la logique de dérivation.
 * - `format=svg` renvoie un graphique autonome, utilisable dans un
 *   `<img src>`, un README ou un message. Le SVG embarque ses propres
 *   couleurs et sa propre requête de préférence sombre : il n'a besoin
 *   d'aucune feuille de style extérieure, ce qui est la condition pour
 *   fonctionner hors du site.
 *
 * Les séries sont calculées par `lib/series.ts`, le même module que le
 * dashboard. Une courbe servie ici ne peut donc pas diverger de celle qui
 * est affichée sur la page.
 */
import { getBacktest, getProbe, getSnapshot, getTrades, seuils } from "@/lib/data";
import {
  calibration,
  courbesIC,
  cumulBacktest,
  cumulTradesPapier,
  distributionR,
  excursions,
  serieEquite,
  universSigne,
} from "@/lib/series";
import { cors, jsonResponse, noStore, preflight } from "@/lib/api";
import {
  BarresUnivers,
  Calibration,
  CourbeCapital,
  CourbeCumulR,
  Distribution,
  NuageExcursions,
} from "@/components/charts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const OPTIONS = preflight;

/**
 * Rendu d'un élément React en balisage.
 *
 * L'import est différé et non statique : Next refuse `react-dom/server` en
 * import de tête dans le routeur applicatif, parce qu'un composant de page
 * qui l'utiliserait contournerait le rendu du framework. Ici, il ne s'agit
 * pas d'une page mais d'une route qui produit un fichier image — le rendu
 * manuel est exactement ce qu'on veut, et il ne se produit que dans le
 * gestionnaire, jamais à l'importation.
 */
async function balisage(element: React.ReactElement): Promise<string> {
  const { renderToStaticMarkup } = await import("react-dom/server");
  return renderToStaticMarkup(element);
}

const SERIES = [
  "capital",
  "cumul_papier",
  "cumul_backtest",
  "distribution",
  "calibration",
  "excursions",
  "ic",
  "univers",
] as const;
type Serie = (typeof SERIES)[number];

/**
 * Palette du SVG autonome.
 *
 * Reprise à l'identique de `globals.css`. Elle est dupliquée à dessein : un
 * SVG servi dans un `<img>` n'a accès à aucune feuille de style extérieure,
 * et une image qui hériterait des variables de la page appelante afficherait
 * n'importe quoi ailleurs.
 */
const STYLE = `
svg{--ink:#16181b;--ink-2:#56585e;--ink-3:#8b8d94;--ink-4:#b4b6bb;
--line:#e6e5e0;--line-2:#d5d4cd;--surface:#fff;--surface-3:#eeeeea;
--up:#116b43;--up-bg:#eff7f1;--up-line:#b9dcc7;
--down:#a8342a;--down-bg:#fbf1f0;--down-line:#e8c3bf;
background:#fbfbf9;font-family:ui-sans-serif,system-ui,sans-serif}
@media (prefers-color-scheme:dark){svg{
--ink:#eceef1;--ink-2:#a2a7af;--ink-3:#6f757e;--ink-4:#4c525a;
--line:#24272d;--line-2:#363a42;--surface:#131519;--surface-3:#23262c;
--up:#4fd08a;--up-bg:#0d2018;--up-line:#1d4432;
--down:#f0837a;--down-bg:#241211;--down-line:#4d2320;
background:#0b0c0e}}
.c-grid{stroke:var(--line);stroke-width:1;shape-rendering:crispEdges}
.c-axis{stroke:var(--line-2);stroke-width:1;shape-rendering:crispEdges}
.c-zero{stroke:var(--ink-4);stroke-width:1;stroke-dasharray:2 3}
.c-tick{fill:var(--ink-3);font-family:ui-monospace,monospace;font-size:9.5px}
.c-label{fill:var(--ink-2);font-size:10px}
.c-line{fill:none;stroke:var(--ink);stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round}
.c-line.thin{stroke-width:1}.c-line.up{stroke:var(--up)}.c-line.down{stroke:var(--down)}
.c-line.ghost{stroke:var(--ink-4);stroke-dasharray:3 3}
.c-area{stroke:none}
.c-dot{fill:var(--ink)}.c-dot.up{fill:var(--up)}.c-dot.down{fill:var(--down)}
.c-bar{fill:var(--ink-3)}.c-bar.up{fill:var(--up)}.c-bar.down{fill:var(--down)}
.c-bar.faint{fill:var(--ink-4)}
`.replace(/\n/g, "");

/**
 * Transforme le balisage d'un composant en document SVG autonome.
 *
 * Le composant produit un fragment destiné à une page qui porte déjà ses
 * styles ; il manque l'espace de noms XML et la feuille embarquée. Les deux
 * sont injectés juste après la balise ouvrante plutôt que passés en
 * propriétés, pour que les composants restent identiques dans les deux
 * contextes — un graphique qui différerait selon son mode de rendu finirait
 * par diverger.
 */
function documentSvg(markup: string): string {
  const fin = markup.indexOf(">");
  if (fin < 0) return markup;
  const ouvrante = markup.slice(0, fin);
  const reste = markup.slice(fin + 1);
  return (
    `<?xml version="1.0" encoding="UTF-8"?>` +
    `${ouvrante} xmlns="http://www.w3.org/2000/svg">` +
    `<style>${STYLE}</style>` +
    reste
  );
}

function svgResponse(markup: string): Response {
  return new Response(documentSvg(markup), {
    status: 200,
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      ...cors,
      ...noStore,
    },
  });
}

const entier = (v: string | null, defaut: number, min: number, max: number) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, Math.round(n))) : defaut;
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const demandee = url.searchParams.get("serie") as Serie | null;
  const format = url.searchParams.get("format") ?? "json";

  if (demandee && !SERIES.includes(demandee)) {
    return jsonResponse(
      { error: `série inconnue : ${demandee}`, series_disponibles: SERIES },
      400,
    );
  }

  const [snap, trades, bt, probe] = await Promise.all([
    getSnapshot(),
    getTrades(),
    getBacktest(),
    getProbe(),
  ]);

  if (!snap) return jsonResponse({ error: "aucun scan disponible" }, 503);

  const capital = serieEquite(snap.portfolio);
  const papier = cumulTradesPapier(trades);
  const backtest = cumulBacktest(bt);
  const rsBacktest = (bt?.trades ?? []).map((t) => t.r_multiple);
  // Taux de réussite en deçà duquel le R/R moyen ne couvre plus les pertes :
  // sans lui, une barre de réussite observée ne dit pas si elle suffit.
  const seuilRentabilite =
    bt?.effet_de_la_structure?.adosse_a_la_structure?.seuil_rentabilite;

  /* ---------------- rendu graphique ---------------- */
  if (format === "svg") {
    if (!demandee) {
      return jsonResponse(
        { error: "format=svg exige un paramètre serie", series_disponibles: SERIES },
        400,
      );
    }
    const w = entier(url.searchParams.get("w"), 860, 240, 1600);
    const h = entier(url.searchParams.get("h"), 240, 120, 900);

    switch (demandee) {
      case "capital":
        return svgResponse(
          await balisage(<CourbeCapital serie={capital} w={w} h={h} />),
        );
      case "cumul_papier":
        return svgResponse(
          await balisage(<CourbeCumulR serie={papier} w={w} h={h} seed="api-p" />),
        );
      case "cumul_backtest":
        if (!backtest) return jsonResponse({ error: "aucun backtest disponible" }, 503);
        return svgResponse(
          await balisage(<CourbeCumulR serie={backtest} w={w} h={h} seed="api-b" />),
        );
      case "distribution":
        return svgResponse(
          await balisage(
            <Distribution paniers={distributionR(rsBacktest)} w={w} h={h} />,
          ),
        );
      case "calibration":
        return svgResponse(
          await balisage(
            <Calibration points={calibration(bt)} seuil={seuilRentabilite} w={w} h={h} />,
          ),
        );
      case "excursions":
        return svgResponse(
          await balisage(<NuageExcursions points={excursions(bt)} w={w} h={h} />),
        );
      case "univers":
        return svgResponse(
          await balisage(<BarresUnivers items={universSigne(snap)} w={w} />),
        );
      case "ic":
        // Les petites multiples sont une grille de plusieurs SVG : elles n'ont
        // pas de forme autonome. On renvoie la donnée plutôt qu'une image
        // tronquée.
        return jsonResponse(
          {
            error: "la série ic n'a pas de rendu SVG unique",
            raison: "elle se compose d'un graphique par facteur",
            json: `${url.origin}/api/curves?serie=ic`,
          },
          422,
        );
    }
  }

  /* ---------------- données ---------------- */
  const toutes = {
    capital: {
      unite: "monnaie du portefeuille",
      axe_x: "horodatage du scan",
      initial: capital.initial,
      final: capital.final,
      sommet: capital.sommet,
      repli_max: capital.repli_max,
      points: capital.points,
      repli: capital.repli,
    },
    cumul_papier: {
      unite: "R",
      axe_x: "numéro de trade fermé",
      total: papier.total,
      repli_max: papier.repli_max,
      gagnants: papier.gagnants,
      perdants: papier.perdants,
      points: papier.points,
    },
    cumul_backtest: backtest && {
      unite: "R",
      axe_x: "numéro de trade rejoué",
      total: backtest.total,
      repli_max: backtest.repli_max,
      gagnants: backtest.gagnants,
      perdants: backtest.perdants,
      points: backtest.points,
    },
    distribution: {
      unite: "effectif",
      axe_x: "résultat en R",
      paniers: distributionR(rsBacktest),
    },
    calibration: {
      unite: "%",
      axe_x: "tranche de score",
      points: calibration(bt),
      seuil_rentabilite: seuilRentabilite ?? null,
      correlation_score_esperance: bt?.calibration?.correlation_score_esperance ?? null,
    },
    excursions: {
      unite: "R",
      axe_x: "pire excursion défavorable",
      axe_y: "meilleure excursion favorable",
      points: excursions(bt),
    },
    ic: {
      unite: "corrélation de rang",
      axe_x: "horizon en bougies",
      observations: probe?.coefficients?.observations ?? 0,
      courbes: courbesIC(probe),
    },
    univers: {
      unite: "score signé",
      // Publié par le scan, pas figé ici : le seuil est réglable, et un
      // tableur qui tracerait l'ancienne ligne décrirait une autre stratégie.
      seuil: seuils(snap).signal,
      points: universSigne(snap),
    },
  };

  if (demandee) {
    return jsonResponse({
      generated_at: snap.generated_at,
      serie: demandee,
      ...toutes[demandee],
    });
  }

  return jsonResponse({
    generated_at: snap.generated_at,
    series_disponibles: SERIES,
    usage: {
      json: "/api/curves?serie=capital",
      svg: "/api/curves?serie=capital&format=svg&w=860&h=240",
      note:
        "Le SVG est autonome : il embarque ses couleurs et suit la préférence " +
        "sombre du lecteur. Il s'utilise directement dans une balise img.",
    },
    ...toutes,
  });
}
