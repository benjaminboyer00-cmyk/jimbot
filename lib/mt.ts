/**
 * Correspondance entre les symboles internes et ceux de MetaTrader.
 *
 * Il n'existe pas de nomenclature standard : chaque courtier nomme ses
 * instruments à sa façon (le S&P 500 est « US500 » chez l'un, « SPX500 » ou
 * « USA500 » chez l'autre, et les paires portent souvent un suffixe comme
 * « .r », « m » ou « _i »). On expose donc plusieurs alias par instrument et
 * on laisse le client retenir celui que son courtier reconnaît.
 */
export const MT_ALIASES: Record<string, string[]> = {
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

/** Alias principal, celui que la plupart des courtiers reconnaissent. */
export function mtSymbol(symbol: string): string {
  return MT_ALIASES[symbol]?.[0] ?? symbol.replace("-", "");
}

/** Nombre de décimales cohérent avec l'ordre de grandeur du prix. */
export function mtDigits(price: number): number {
  if (price >= 1000) return 2;
  if (price >= 100) return 3;
  if (price >= 1) return 5;
  return 8;
}
