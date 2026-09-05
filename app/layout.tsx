import type { Metadata, Viewport } from "next";
import { Instrument_Sans, JetBrains_Mono, Newsreader } from "next/font/google";
import "./globals.css";

/**
 * Trois familles, trois rôles, aucun recouvrement.
 *
 * - Instrument Sans porte l'interface : une grotesque légèrement resserrée,
 *   dense sans être étroite, qui tient dans les libellés de tableau.
 * - JetBrains Mono porte **tous** les chiffres. Un prix, un R, un
 *   coefficient : la chasse fixe aligne les colonnes et rend comparables deux
 *   nombres qu'on lit l'un sous l'autre.
 * - Newsreader porte le texte rédigé — briefing, résumé de presse, notes de
 *   méthode. La distinction typographique dit ce qui est calculé et ce qui
 *   est écrit, sans avoir à le préciser.
 */
const sans = Instrument_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

const serif = Newsreader({
  subsets: ["latin"],
  display: "swap",
  style: ["normal", "italic"],
  variable: "--font-serif",
});

/**
 * Base des URL absolues.
 *
 * Un aperçu de partage se réclame par une adresse absolue : un chemin relatif
 * ne sert à rien à Discord, qui ne connaît pas le site. `VERCEL_URL` porte le
 * domaine du déploiement courant — y compris pour une prévisualisation, ce qui
 * évite qu'une branche de test annonce l'image de production.
 */
const BASE = process.env.VERCEL_URL
  ? `https://${process.env.VERCEL_URL}`
  : "https://jimbot-seven.vercel.app";

const TITRE = "Jimbot — analyse de marché";
const DESCRIPTION =
  "Moteur d'analyse quantitative multi-actifs : crypto, forex, indices et memecoins. " +
  "Chaque signal émis est suivi jusqu'à son issue.";

export const metadata: Metadata = {
  metadataBase: new URL(BASE),
  title: TITRE,
  description: DESCRIPTION,
  // L'aperçu est produit par `app/opengraph-image.tsx` : Next le référence
  // tout seul, il n'y a pas d'URL à écrire ici.
  openGraph: {
    type: "website",
    locale: "fr_FR",
    siteName: "Jimbot",
    title: TITRE,
    description: DESCRIPTION,
    url: "/",
  },
  twitter: { card: "summary_large_image", title: TITRE, description: DESCRIPTION },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f5f0" },
    { media: "(prefers-color-scheme: dark)", color: "#15171b" },
  ],
};

/**
 * Applique le thème enregistré avant la première peinture.
 *
 * Sans cela, un lecteur en thème sombre verrait un éclair blanc à chaque
 * navigation : le HTML est servi en clair puis corrigé par React. Le script
 * est synchrone et volontairement minuscule.
 */
const THEME_BOOT = `
try {
  var t = localStorage.getItem("jimbot-theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="fr"
      className={`${sans.variable} ${mono.variable} ${serif.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <a className="skip" href="#contenu">
          Aller au contenu
        </a>
        {children}
      </body>
    </html>
  );
}
