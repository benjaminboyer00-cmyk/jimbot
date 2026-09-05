/**
 * Icône iOS / iPadOS.
 *
 * Le format impose du PNG opaque de 180 px : Safari ne lit pas le SVG de
 * `icon.svg` et n'applique aucun fond, si bien qu'une figure transparente
 * sortirait en noir sur noir sur un écran d'accueil sombre. On redessine donc
 * la même marque, à une échelle où le pointillé du niveau tient encore.
 */
import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          width: "100%",
          height: "100%",
          background: "#16181b",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* iOS applique son propre masque arrondi : une figure qui touche
            les bords serait rognée. On la rentre donc de 26 %. */}
        <svg width="132" height="132" viewBox="0 0 32 32">
          <path
            d="M5 20.5h22"
            stroke="#8b8d94"
            strokeWidth="1.6"
            strokeDasharray="3.2 3.2"
            strokeLinecap="round"
          />
          <path
            d="M6 26l5-5.5 4.5 3.5L25 7.5"
            fill="none"
            stroke="#fbfbf9"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="25" cy="7.5" r="3" fill="#4fd08a" />
        </svg>
      </div>
    ),
    size,
  );
}
