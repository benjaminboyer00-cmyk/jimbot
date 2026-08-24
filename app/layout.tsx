import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Jimbot — analyse de marché",
  description:
    "Moteur d'analyse quantitative multi-actifs : crypto, forex, indices et memecoins.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
