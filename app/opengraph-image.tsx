/**
 * Aperçu de partage.
 *
 * Un lien collé dans un salon Discord ou un fil de discussion n'affichait
 * jusqu'ici qu'une vignette vide. C'était doublement dommage : le site n'avait
 * aucune existence visuelle hors de lui-même, et le contexte manquait au
 * moment précis où quelqu'un décide s'il clique.
 *
 * L'image n'est donc pas un logo sur fond uni mais **l'état du marché au
 * dernier scan** : le score signé de chaque actif suivi, autour de zéro, avec
 * les seuils de déclenchement. C'est la même figure que la section « univers
 * suivi » de la page, réduite à ce qui se lit à 600 pixels de large.
 *
 * Le bandeau du bas porte la redevabilité — ce que les signaux émis ont donné
 * — plutôt qu'une promesse. Si l'image doit convaincre quelqu'un de cliquer,
 * autant que ce soit sur un chiffre mesuré.
 *
 * Rendu en PNG et non en SVG : Discord, Slack et les réseaux sociaux ne
 * rendent pas le SVG dans un aperçu, quand bien même le reste du site n'est
 * fait que de ça.
 */
import { ImageResponse } from "next/og";

import { fmtNum, getSnapshot, getSuivi, seuils } from "@/lib/data";
import { universSigne } from "@/lib/series";

export const alt = "Jimbot — état du marché au dernier scan";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** Palette, reprise de `globals.css`. L'image ne suit pas le thème du lecteur :
 *  elle est servie identique à tout le monde, on la fige donc en sombre. */
const C = {
  fond: "#0b0c0e",
  surface: "#131519",
  ligne: "#24272d",
  ligne2: "#363a42",
  encre: "#eceef1",
  encre2: "#a2a7af",
  encre3: "#6f757e",
  haut: "#4fd08a",
  bas: "#f0837a",
};

const DEMI = 96;

export default async function Image() {
  const [snap, suivi] = await Promise.all([getSnapshot(), getSuivi()]);
  const seuil = seuils(snap);
  const points = snap ? universSigne(snap) : [];
  const c = snap?.counts;

  const titre = !snap
    ? "En attente du premier scan"
    : c && c.actionable > 0
      ? `${c.actionable} signal${c.actionable > 1 ? "s" : ""} · ${c.long} achat · ${c.short} vente`
      : "Aucun signal — le moteur reste à l’écart";

  const r = suivi?.resume;
  const bilan =
    r && r.tranches > 0
      ? `${r.signaux} signaux émis · ${r.tranches} tranchés · ${fmtNum(r.win_rate ?? 0, 0)} % de réussite · ${
          (r.esperance_r ?? 0) >= 0 ? "+" : ""
        }${fmtNum(r.esperance_r ?? 0)} R d’espérance`
      : "Suivi des signaux émis en cours de constitution";

  const date = snap
    ? new Date(snap.generated_at).toLocaleString("fr-FR", {
        day: "2-digit",
        month: "long",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      }) + " UTC"
    : "";

  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          background: C.fond,
          color: C.encre,
          padding: "52px 56px",
          fontFamily: "sans-serif",
        }}
      >
        {/* Bandeau */}
        <div style={{ display: "flex", alignItems: "center", width: "100%" }}>
          <svg width="38" height="38" viewBox="0 0 32 32">
            <path
              d="M4 21h24"
              stroke={C.encre3}
              strokeWidth="1.6"
              strokeDasharray="3.2 3.2"
              strokeLinecap="round"
            />
            <path
              d="M5 27l5.5-6 4.5 3.5L26 6.5"
              fill="none"
              stroke={C.encre}
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="26" cy="6.5" r="3" fill={C.haut} />
          </svg>
          <div
            style={{
              fontSize: 30,
              fontWeight: 700,
              letterSpacing: "0.16em",
              marginLeft: 16,
            }}
          >
            JIMBOT
          </div>
          <div style={{ fontSize: 22, color: C.encre3, marginLeft: 18 }}>
            analyse de marché
          </div>
          <div
            style={{
              fontSize: 20,
              color: C.encre3,
              marginLeft: "auto",
            }}
          >
            {date}
          </div>
        </div>

        <div style={{ fontSize: 52, fontWeight: 600, marginTop: 34, letterSpacing: "-0.02em" }}>
          {titre}
        </div>
        {/* Satori exige un `display` explicite sur tout élément à plusieurs
            enfants : une phrase entrecoupée d'une valeur en compte trois. On
            assemble donc le texte avant, plutôt que dans le balisage. */}
        <div style={{ fontSize: 22, color: C.encre2, marginTop: 10 }}>
          {`Score signé de chaque actif suivi — achat au-dessus de zéro, vente en dessous. Seuil de déclenchement à ±${seuil.signal.toFixed(0)}.`}
        </div>

        {/* Univers */}
        <div
          style={{
            display: "flex",
            position: "relative",
            width: "100%",
            height: DEMI * 2,
            marginTop: 26,
            alignItems: "center",
          }}
        >
          {/* Seuils et zéro, en fond. */}
          {[seuil.signal, -seuil.signal].map((v) => (
            <div
              key={v}
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                top: DEMI - (v / 100) * DEMI,
                height: 1,
                background: C.ligne2,
              }}
            />
          ))}
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: DEMI,
              height: 1,
              background: C.encre3,
            }}
          />
          {points.map((p) => {
            const h = Math.max(2, (Math.min(100, Math.abs(p.score)) / 100) * DEMI);
            const positif = p.score >= 0;
            return (
              <div
                key={p.symbol}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  flex: 1,
                  height: "100%",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    height: DEMI,
                    width: "100%",
                    alignItems: "flex-end",
                    justifyContent: "center",
                  }}
                >
                  <div
                    style={{
                      width: 26,
                      height: positif ? h : 0,
                      background: C.haut,
                      opacity: Math.abs(p.score) >= seuil.signal ? 1 : 0.45,
                    }}
                  />
                </div>
                <div
                  style={{
                    display: "flex",
                    height: DEMI,
                    width: "100%",
                    alignItems: "flex-start",
                    justifyContent: "center",
                  }}
                >
                  <div
                    style={{
                      width: 26,
                      height: positif ? 0 : h,
                      background: C.bas,
                      opacity: Math.abs(p.score) >= seuil.signal ? 1 : 0.45,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ display: "flex", width: "100%", marginTop: 6 }}>
          {points.map((p) => (
            <div
              key={p.symbol}
              style={{
                display: "flex",
                flex: 1,
                justifyContent: "center",
                fontSize: 15,
                color: C.encre3,
              }}
            >
              {p.symbol.replace("-USD", "")}
            </div>
          ))}
        </div>

        {/* Redevabilité */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            width: "100%",
            marginTop: "auto",
            paddingTop: 22,
            borderTop: `1px solid ${C.ligne}`,
          }}
        >
          <div style={{ fontSize: 21, color: C.encre2 }}>{bilan}</div>
          <div style={{ fontSize: 18, color: C.encre3, marginLeft: "auto" }}>
            Ne constitue pas un conseil en investissement
          </div>
        </div>
      </div>
    ),
    size,
  );
}
