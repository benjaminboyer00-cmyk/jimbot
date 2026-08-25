/** Utilitaires communs aux routes d'API. */

/**
 * En-têtes CORS permissifs.
 *
 * L'API est en lecture seule et ne sert que des données déjà publiques dans le
 * dépôt : il n'y a rien à protéger, et l'ouvrir permet à un robot, un tableur
 * ou une page tierce de la consommer directement. MetaTrader ignore CORS, mais
 * un client navigateur en a besoin.
 */
export const cors: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

/** Les données changent à chaque scan : aucun cache intermédiaire. */
export const noStore: Record<string, string> = {
  "Cache-Control": "no-store, max-age=0",
};

export function jsonResponse(
  body: unknown,
  status = 200,
  extra: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...cors,
      ...noStore,
      ...extra,
    },
  });
}

export function preflight(): Response {
  return new Response(null, { status: 204, headers: cors });
}
