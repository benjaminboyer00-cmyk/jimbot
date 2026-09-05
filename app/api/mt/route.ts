/**
 * `GET /api/mt` — flux de signaux au format MetaTrader.
 *
 * Conçu pour être consommé par un Expert Advisor via `WebRequest` : réponse
 * plate, champs courts, aucune imbrication inutile, et surtout **aucun ordre
 * implicite**. L'EA reçoit une description de configuration ; c'est lui, et
 * l'utilisateur derrière lui, qui décident d'agir.
 *
 * Paramètres :
 *   ?mode=actionable   (défaut) uniquement les signaux franchissant le seuil
 *   ?mode=watchlist    uniquement la liste de surveillance
 *   ?mode=all          les deux
 *   ?min_score=70      relève le seuil de conviction
 *   ?symbol=XAUUSD     filtre sur un instrument (nom interne ou alias MT)
 */
import { getSnapshot, seuils, type Signal } from "@/lib/data";
import { jsonResponse, noStore, preflight } from "@/lib/api";
import { mtAliases, mtSymbol, mtDigits, tableAlias } from "@/lib/mt";

export const dynamic = "force-dynamic";
export const OPTIONS = preflight;

type Mode = "actionable" | "watchlist" | "all";

function toMt(s: Signal, actionable: boolean, alias: Record<string, string[]>) {
  const digits = mtDigits(s.price);
  const round = (v: number) => Number(v.toFixed(digits));
  const dir = actionable ? s.direction : s.bias;

  return {
    symbol: mtSymbol(s.symbol, alias),
    aliases: mtAliases(s.symbol, alias),
    internal: s.symbol,
    name: s.label,
    // BUY / SELL : la convention MetaTrader, pas la nôtre.
    cmd: dir === "long" ? "BUY" : dir === "short" ? "SELL" : "NONE",
    price: round(s.price),
    entry: round(s.entry),
    sl: round(s.stop),
    tp: round(s.target),
    digits,
    rr: s.rr,
    score: Math.round(s.score),
    win_prob: s.win_prob,
    expected_r: s.expected_r,
    // Fraction du capital à risquer si le stop est touché. L'EA en déduit son
    // volume : c'est la distance au stop qui détermine la taille, jamais un
    // nombre de lots fixe.
    risk_pct: actionable ? Number((Math.min(1, s.score / 100) * 1.0).toFixed(2)) : 0,
    atr_pct: s.atr_pct,
    regime: s.regime.name,
    timeframe: s.timeframe,
    actionable,
    stop_basis: s.stop_basis,
    target_basis: s.target_basis,
    warnings: s.warnings,
  };
}

export async function GET(request: Request) {
  const snap = await getSnapshot();
  if (!snap) return jsonResponse({ error: "aucun scan disponible" }, 503);

  const url = new URL(request.url);
  const mode = (url.searchParams.get("mode") ?? "actionable") as Mode;
  const minScore = Number(url.searchParams.get("min_score") ?? "0");
  const symbolFilter = url.searchParams.get("symbol")?.toUpperCase();

  // La table d'alias vient du moteur, qui est celui qui passe les ordres.
  const alias = tableAlias(snap.mt_aliases);
  const seuil = seuils(snap);
  const actionable = snap.signals.filter((s) => s.actionable);
  const watchlist = snap.watchlist ?? [];

  let rows =
    mode === "watchlist"
      ? watchlist.map((s) => toMt(s, false, alias))
      : mode === "all"
        ? [...actionable.map((s) => toMt(s, true, alias)),
           ...watchlist.map((s) => toMt(s, false, alias))]
        : actionable.map((s) => toMt(s, true, alias));

  if (minScore > 0) rows = rows.filter((r) => r.score >= minScore);
  if (symbolFilter) {
    rows = rows.filter(
      (r) =>
        r.internal.toUpperCase() === symbolFilter ||
        r.aliases.some((a) => a.toUpperCase() === symbolFilter),
    );
  }

  return jsonResponse(
    {
      generated_at: snap.generated_at,
      // Horodatage Unix : plus simple à comparer côté MQL.
      timestamp: Math.floor(new Date(snap.generated_at).getTime() / 1000),
      mode,
      count: rows.length,
      // Seuils en vigueur, pour que l'EA puisse afficher le contexte. Ils
      // viennent du scan et non d'une constante : ils sont réglables par
      // variable d'environnement, et un robot qui afficherait l'ancien seuil
      // décrirait une stratégie qui n'est plus celle qui tourne.
      thresholds: { signal: seuil.signal, alert: seuil.alerte },
      risk_off: snap.risk_off?.level ?? 0,
      signals: rows,
      disclaimer:
        "Signaux informatifs. Aucun ordre n'est transmis par cette API. " +
        "Toute exécution relève de la responsabilité de l'utilisateur.",
    },
    200,
    noStore,
  );
}
