import Link from "next/link";
import { ThemeToggle } from "./theme-toggle";

/**
 * Marque.
 *
 * Un niveau horizontal traversé par une progression en escalier : c'est
 * exactement ce que fait le moteur — placer un plan autour d'un niveau de
 * structure. Dessiné en trait pour rester lisible à 22 px et suivre la
 * couleur du texte dans les deux thèmes.
 */
function Mark() {
  return (
    <svg className="mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M2 15.5h20"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeDasharray="2.5 2.5"
        opacity="0.45"
      />
      <path
        d="M3 19.5l4.5-5 4 3.2L20 4.5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="20" cy="4.5" r="1.9" fill="currentColor" />
    </svg>
  );
}

export type OngletActif = "tableau" | "courbes";

const ONGLETS: { href: string; label: string; cle: OngletActif }[] = [
  { href: "/", label: "Tableau de bord", cle: "tableau" },
  { href: "/courbes", label: "Courbes", cle: "courbes" },
];

export function Topbar({
  stamp,
  generatedAt,
  actif,
}: {
  stamp: string;
  generatedAt?: string;
  actif: OngletActif;
}) {
  // Fraîcheur des données. L'ordonnanceur GitHub étrangle les planifications
  // rapprochées : une exécution peut être retardée de plusieurs heures. Mieux
  // vaut l'afficher que laisser croire à une surveillance continue.
  const minutes = generatedAt
    ? (Date.now() - new Date(generatedAt).getTime()) / 60000
    : 0;
  const perime = minutes > 90;

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link className="brand" href="/">
          <Mark />
          JIMBOT<span>analyse de marché</span>
        </Link>

        <nav className="nav" aria-label="Sections">
          {ONGLETS.map((o) => (
            <Link
              key={o.href}
              href={o.href}
              aria-current={o.cle === actif ? "page" : undefined}
            >
              {o.label}
            </Link>
          ))}
          <a href="/api/signals" target="_blank" rel="noopener noreferrer">
            API
          </a>
        </nav>

        <div className="topbar-right">
          <span className="stamp">
            {perime && <span className="stale">données anciennes</span>}
            {generatedAt && <span className={`pulse${perime ? " cold" : ""}`} />}
            {stamp}
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
