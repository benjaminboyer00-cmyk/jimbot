"use client";

import { useEffect, useState } from "react";

type Mode = "light" | "dark" | "system";

const CLE = "jimbot-theme";

/**
 * Bascule clair / sombre / système.
 *
 * Trois états et non deux : quelqu'un qui laisse son système décider doit
 * pouvoir y revenir après avoir essayé les deux autres. Le choix est écrit
 * dans `localStorage` et relu par un script synchrone dans `<head>`, ce qui
 * évite l'éclair blanc au chargement.
 */
export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>("system");
  const [monte, setMonte] = useState(false);

  useEffect(() => {
    setMonte(true);
    try {
      const t = localStorage.getItem(CLE);
      if (t === "light" || t === "dark") setMode(t);
    } catch {
      /* navigation privée : on reste sur le thème système */
    }
  }, []);

  function suivant() {
    const ordre: Mode[] = ["system", "light", "dark"];
    const m = ordre[(ordre.indexOf(mode) + 1) % 3];
    setMode(m);
    try {
      if (m === "system") {
        localStorage.removeItem(CLE);
        delete document.documentElement.dataset.theme;
      } else {
        localStorage.setItem(CLE, m);
        document.documentElement.dataset.theme = m;
      }
    } catch {
      /* l'écriture peut échouer, l'attribut a déjà été posé */
    }
  }

  const libelle =
    mode === "system" ? "Thème : système" : mode === "light" ? "Thème : clair" : "Thème : sombre";

  return (
    <button
      className="themetoggle"
      onClick={suivant}
      title={libelle}
      aria-label={`${libelle} — cliquer pour changer`}
      type="button"
    >
      {/* Avant l'hydratation, on n'affiche aucune icône : montrer « clair »
          puis basculer sur « sombre » serait un scintillement de plus. */}
      {!monte ? (
        <svg viewBox="0 0 16 16" aria-hidden="true" />
      ) : mode === "system" ? (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
          <rect x="1.5" y="2.5" width="13" height="9" rx="1.5" />
          <path d="M5.5 14h5" strokeLinecap="round" />
        </svg>
      ) : mode === "light" ? (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" />
          <path
            d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2L3.1 3.1"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
          <path d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6a5.8 5.8 0 1 0 7 7Z" strokeLinejoin="round" />
        </svg>
      )}
    </button>
  );
}
