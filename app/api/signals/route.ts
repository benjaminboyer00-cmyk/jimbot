/**
 * `GET /api/signals` — état complet du dernier scan, en JSON.
 *
 * Destiné aux intégrations qui veulent tout : signaux déclenchables, liste de
 * surveillance, contexte géopolitique, portefeuille. Pour un robot de trading,
 * `/api/mt` est plus adapté : il ne renvoie que ce qui est exécutable.
 */
import { getSnapshot } from "@/lib/data";
import { jsonResponse, noStore, preflight } from "@/lib/api";

export const dynamic = "force-dynamic";
export const OPTIONS = preflight;

export async function GET(request: Request) {
  const snap = await getSnapshot();
  if (!snap) {
    return jsonResponse({ error: "aucun scan disponible" }, 503);
  }

  const url = new URL(request.url);
  // `?actionable=1` ne renvoie que les signaux franchissant le seuil.
  const onlyActionable = url.searchParams.get("actionable") === "1";
  const signals = onlyActionable
    ? snap.signals.filter((s) => s.actionable)
    : snap.signals;

  return jsonResponse(
    {
      generated_at: snap.generated_at,
      counts: snap.counts,
      regimes: snap.regimes,
      risk_off: snap.risk_off ?? null,
      speeches: snap.speeches ?? [],
      news_summary: snap.news_summary ?? "",
      signals,
      watchlist: snap.watchlist ?? [],
      portfolio: {
        equity: snap.portfolio.equity,
        initial: snap.portfolio.initial,
        open_risk: snap.portfolio.open_risk,
        positions: snap.portfolio.positions,
      },
      disclaimer:
        "Analyse automatisée à but informatif. Ne constitue pas un conseil en investissement.",
    },
    200,
    noStore,
  );
}
