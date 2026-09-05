/**
 * Correspondance entre les symboles internes et ceux de MetaTrader.
 *
 * Il n'existe pas de nomenclature standard : chaque courtier nomme ses
 * instruments à sa façon (le S&P 500 est « US500 » chez l'un, « SPX500 » ou
 * « USA500 » chez l'autre, et les paires portent souvent un suffixe comme
 * « .r », « m » ou « _i »). On expose donc plusieurs alias par instrument et
 * on laisse le client retenir celui que son courtier reconnaît.
 *
 * **La table fait foi côté moteur** (`engine/jimbot/mt_symbols.py`), parce que
 * c'est lui qui passe les ordres, et elle est publiée dans l'instantané. Celle
 * qui suit n'est qu'un repli pour les instantanés antérieurs à cette
 * publication — même rôle que `RISQUE_PAR_DEFAUT` dans `lib/sizing.ts`. Deux
 * tables tenues en parallèle auraient divergé au premier courtier ajouté, et
 * un ordre serait parti sur le mauvais instrument.
 */
export const MT_ALIASES_REPLI: Record<string, string[]> = {
  XAUUSD: ["XAUUSD", "GOLD", "XAUUSD.r", "XAUUSDm"],
  EURUSD: ["EURUSD", "EURUSD.r", "EURUSDm"],
  GBPUSD: ["GBPUSD", "GBPUSD.r", "GBPUSDm"],
  USDJPY: ["USDJPY", "USDJPY.r", "USDJPYm"],
  SPX: ["US500", "SPX500", "USA500", "S&P500"],
  NDX: ["NAS100", "USTEC", "NDX100", "USATEC"],
  DXY: ["USDX", "DXY", "USDOLLAR"],
  VIX: ["VIX", "VOLX"],
  "BTC-USD": ["BTCUSD", "BTCUSDT", "BTCUSD.r"],
  "ETH-USD": ["ETHUSD", "ETHUSDT"],
  "SOL-USD": ["SOLUSD", "SOLUSDT"],
  "BNB-USD": ["BNBUSD", "BNBUSDT"],
  "XRP-USD": ["XRPUSD", "XRPUSDT"],
  "DOGE-USD": ["DOGEUSD", "DOGEUSDT"],
  "AVAX-USD": ["AVAXUSD", "AVAXUSDT"],
  "LINK-USD": ["LINKUSD", "LINKUSDT"],
};

/** Table en vigueur : celle du moteur si l'instantané la porte, sinon le repli. */
export function tableAlias(publiee?: Record<string, string[]> | null): Record<string, string[]> {
  return publiee && Object.keys(publiee).length ? publiee : MT_ALIASES_REPLI;
}

/** Alias connus d'un instrument, du plus courant au plus rare. */
export function mtAliases(
  symbol: string,
  table: Record<string, string[]> = MT_ALIASES_REPLI,
): string[] {
  return table[symbol] ?? [symbol.replace("-", "")];
}

/** Alias principal, celui que la plupart des courtiers reconnaissent. */
export function mtSymbol(
  symbol: string,
  table: Record<string, string[]> = MT_ALIASES_REPLI,
): string {
  return mtAliases(symbol, table)[0];
}

/** Nombre de décimales cohérent avec l'ordre de grandeur du prix. */
export function mtDigits(price: number): number {
  if (price >= 1000) return 2;
  if (price >= 100) return 3;
  if (price >= 1) return 5;
  return 8;
}
