/**
 * `GET /api/reports` — index des rapports PDF.
 * `GET /api/reports?file=jimbot-2026-08-25.pdf` — téléchargement du fichier.
 *
 * Les PDF vivent dans le dépôt, pas dans le bundle du dashboard : la route les
 * relaie depuis GitHub. Sans cela, il faudrait redéployer à chaque rapport et
 * l'utilisateur devrait connaître l'URL brute.
 */
import { getSnapshot } from "@/lib/data";
import { cors, jsonResponse, noStore, preflight } from "@/lib/api";

export const dynamic = "force-dynamic";
export const OPTIONS = preflight;

const RAW_BASE =
  process.env.JIMBOT_DATA_URL?.replace(/\/data$/, "") ??
  "https://raw.githubusercontent.com/benjaminboyer00-cmyk/jimbot/main";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const file = url.searchParams.get("file");
  const snap = await getSnapshot();
  const reports = snap?.reports ?? [];

  if (!file) {
    return jsonResponse({
      count: reports.length,
      reports: reports.map((r) => ({
        ...r,
        url: `/api/reports?file=${encodeURIComponent(r.name)}`,
      })),
    });
  }

  // Le nom est repris tel quel dans une URL distante : on le contraint à un
  // motif strict pour qu'aucun chemin ne puisse s'en échapper.
  if (!/^jimbot-\d{4}-\d{2}-\d{2}\.pdf$/.test(file)) {
    return jsonResponse({ error: "nom de rapport invalide" }, 400);
  }

  const upstream = await fetch(`${RAW_BASE}/reports/${file}`, { cache: "no-store" });
  if (!upstream.ok) {
    return jsonResponse({ error: `rapport introuvable : ${file}` }, 404);
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `inline; filename="${file}"`,
      ...cors,
      ...noStore,
    },
  });
}
