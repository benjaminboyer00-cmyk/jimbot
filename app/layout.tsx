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

export const metadata: Metadata = {
  title: "Jimbot — analyse de marché",
  description:
    "Moteur d'analyse quantitative multi-actifs : crypto, forex, indices et memecoins.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbf9" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0c0e" },
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
