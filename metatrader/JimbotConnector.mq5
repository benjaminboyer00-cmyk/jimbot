//+------------------------------------------------------------------+
//|                                          JimbotConnector.mq5     |
//|  Relie MetaTrader 5 au flux de signaux Jimbot.                   |
//|                                                                  |
//|  Par défaut, l'EA n'exécute AUCUN ordre : il lit l'API, affiche  |
//|  les configurations sur le graphique et journalise. L'exécution  |
//|  automatique doit être activée explicitement, et reste soumise   |
//|  aux plafonds de risque définis ci-dessous.                      |
//|                                                                  |
//|  PRÉREQUIS : autoriser l'URL dans MetaTrader —                   |
//|  Outils > Options > Expert Advisors > cocher « Autoriser les     |
//|  WebRequest » et ajouter le domaine de votre déploiement.        |
//+------------------------------------------------------------------+
#property copyright "Jimbot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- Paramètres -----------------------------------------------------
input string   ApiUrl          = "https://jimbot-seven.vercel.app/api/mt";
input string   Mode            = "actionable";  // actionable | watchlist | all
// 0 = suivre le seuil publié par l'API (recommandé).
//
// La valeur précédente, 68, était le seuil d'*alerte* Discord et non le seuil
// de *signal*. Mesuré sur 3 182 relevés, 5,6 % franchissent 58 mais seulement
// 0,06 % franchissent 68 — deux relevés. L'EA livré ainsi ne prenait donc
// pratiquement jamais de position, ce qui ressemblait à une panne alors que
// c'était un réglage. Laisser 0 fait suivre `thresholds.signal`, qui vient du
// scan et se déplace avec lui.
input int      MinScore        = 0;             // 0 = seuil publié par l'API
input int      RefreshSeconds  = 300;           // intervalle d'interrogation
input bool     AutoTrade       = false;         // EXÉCUTION RÉELLE — désactivée par défaut
input double   MaxRiskPercent  = 1.0;           // risque maximal par position, en % du solde
input double   MaxTotalRisk    = 5.0;           // risque cumulé maximal, en % du solde
input int      MaxPositions    = 5;             // positions simultanées maximales
input int      MagicNumber     = 20260825;      // identifiant des ordres de cet EA
input int      SlippagePoints  = 20;
input bool     ShowPanel       = true;          // afficher le panneau sur le graphique
// Notification poussée vers l'application mobile MetaTrader.
//
// L'application mobile n'exécute pas d'Expert Advisor — c'est une limite de la
// plateforme, pas un réglage. Le seul moyen de recevoir les signaux sur un
// téléphone est donc que le terminal de bureau les y pousse. Renseigner
// l'identifiant MetaQuotes du téléphone dans Outils → Options → Notifications,
// puis activer ceci.
input bool     NotifyMobile    = true;          // pousser les signaux vers le mobile
// Auto-test : ouvre une position de volume minimal sur le meilleur plan
// disponible, pour vérifier la chaîne complète — appel de l'API, lecture du
// JSON, correspondance du symbole chez le courtier, calcul du volume, envoi de
// l'ordre, pose du stop et de l'objectif.
//
// Il existe parce que le moteur n'émet un signal que 5,6 % du temps : sans lui,
// on installe l'EA, on ne voit rien se produire pendant deux jours, et on ne
// sait pas distinguer « ça marche et il n'y a rien à prendre » de « c'est
// cassé ». L'auto-test répond à cette question en une minute.
//
// Il REFUSE de s'exécuter sur un compte réel. La vérification porte sur
// ACCOUNT_TRADE_MODE, que le serveur du courtier renseigne — ce n'est pas une
// case à cocher côté client.
input bool     SelfTestOnDemo  = false;         // auto-test, compte démo uniquement

//--- État interne ---------------------------------------------------
CTrade         trade;
datetime       g_last_poll     = 0;
string         g_last_stamp    = "";
int            g_signal_count  = 0;
string         g_panel_lines[];

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(AutoTrade)
      Print("ATTENTION : exécution automatique ACTIVÉE. Risque max ",
            MaxRiskPercent, "% par position, ", MaxTotalRisk, "% au total.");
   else
      Print("Mode lecture seule : aucun ordre ne sera transmis.");

   EventSetTimer(MathMax(30, RefreshSeconds));
   Poll();

   if(SelfTestOnDemo) AutoTest();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Auto-test : la chaîne complète, sur un compte de démonstration    |
//+------------------------------------------------------------------+
void AutoTest()
{
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode != ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("Jimbot AUTO-TEST REFUSÉ : ce compte n'est pas un compte de ",
            "démonstration. L'auto-test ouvre une vraie position et ne ",
            "s'exécute que sur un compte démo.");
      return;
   }

   Print("--- Jimbot : auto-test sur compte de démonstration ---");

   // On interroge `all` : le moteur ne franchit son seuil que 5,6 % du temps,
   // et l'auto-test doit pouvoir s'exécuter n'importe quand. Le plan vient donc
   // de la liste de surveillance — ce n'est pas une recommandation, c'est un
   // plan valide dont on se sert pour éprouver la tuyauterie.
   string body = HttpGet(ApiUrl + "?mode=all");
   if(body == "")
   {
      Print("AUTO-TEST : ÉCHEC à l'étape 1 — aucune réponse de l'API. ",
            "Le domaine est-il autorisé dans Outils > Options > Expert Advisors ?");
      return;
   }
   Print("AUTO-TEST 1/5 : API joignable, ", StringLen(body), " octets reçus.");

   int cle = StringFind(body, "\"signals\"");
   int crochet = (cle < 0) ? -1 : StringFind(body, "[", cle);
   int open_brace = (crochet < 0) ? -1 : StringFind(body, "{", crochet);
   int close_brace = (open_brace < 0) ? -1 : StringFind(body, "}", open_brace);
   if(open_brace < 0 || close_brace < 0)
   {
      Print("AUTO-TEST : ÉCHEC à l'étape 2 — aucun signal dans la réponse. ",
            "L'API a répondu, mais son contenu n'a pas la forme attendue.");
      return;
   }
   string item = StringSubstr(body, open_brace, close_brace - open_brace + 1);
   string cmd  = JsonString(item, "cmd");
   double sl   = JsonNumber(item, "sl");
   double tp   = JsonNumber(item, "tp");
   if(cmd != "BUY" && cmd != "SELL")
   {
      Print("AUTO-TEST : ÉCHEC à l'étape 2 — le premier plan n'a pas de sens ",
            "exploitable (cmd = « ", cmd, " »).");
      return;
   }
   Print("AUTO-TEST 2/5 : plan lu — ", JsonString(item, "internal"), " ", cmd,
         "  SL ", sl, "  TP ", tp);

   string symbol = ResolveSymbol(item);
   if(symbol == "")
   {
      Print("AUTO-TEST : ÉCHEC à l'étape 3 — aucun des alias de cet instrument ",
            "n'existe chez ce courtier. Les autres signaux peuvent très bien ",
            "fonctionner : la nomenclature varie d'un instrument à l'autre.");
      return;
   }
   Print("AUTO-TEST 3/5 : symbole reconnu chez ce courtier — ", symbol);

   double volume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   Print("AUTO-TEST 4/5 : volume minimal du courtier — ", volume,
         " lot(s). L'auto-test s'en tient là, il ne dimensionne pas par le risque.");

   bool ok = (cmd == "BUY")
             ? trade.Buy(volume, symbol, 0.0, sl, tp, "jimbot-autotest")
             : trade.Sell(volume, symbol, 0.0, sl, tp, "jimbot-autotest");

   if(ok)
      Print("AUTO-TEST 5/5 : RÉUSSI — position ouverte sur ", symbol,
            ", ticket ", trade.ResultOrder(),
            ". Elle est visible dans l'onglet Trade et sur votre téléphone. ",
            "Fermez-la à la main : l'auto-test ne la surveille pas.");
   else
      Print("AUTO-TEST : ÉCHEC à l'étape 5 — ", trade.ResultRetcodeDescription(),
            " (code ", trade.ResultRetcode(), "). Le marché est-il ouvert ",
            "pour cet instrument ?");
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, "jimbot_");
}

void OnTimer() { Poll(); }

//+------------------------------------------------------------------+
//| Interroge l'API et traite la réponse                             |
//+------------------------------------------------------------------+
void Poll()
{
   // MinScore = 0 : on ne filtre pas côté requête, l'API applique déjà son
   // propre seuil de signal et le publie dans `thresholds.signal`.
   string url = ApiUrl + "?mode=" + Mode;
   if(MinScore > 0)
      url += "&min_score=" + IntegerToString(MinScore);
   string body = HttpGet(url);
   if(body == "")
   {
      Print("Jimbot : réponse vide ou requête refusée. Vérifiez que le domaine ",
            "est autorisé dans Outils > Options > Expert Advisors.");
      return;
   }

   string stamp = JsonString(body, "generated_at");
   // Un même scan ne doit être traité qu'une fois : sans ce garde-fou, l'EA
   // rouvrirait la même position à chaque interrogation.
   if(stamp == g_last_stamp)
   {
      if(ShowPanel) DrawPanel();
      return;
   }
   g_last_stamp = stamp;
   g_last_poll  = TimeCurrent();

   ProcessSignals(body);
   if(ShowPanel) DrawPanel();
}

//+------------------------------------------------------------------+
//| Découpe le tableau "signals" et traite chaque élément            |
//+------------------------------------------------------------------+
void ProcessSignals(const string body)
{
   ArrayResize(g_panel_lines, 0);
   g_signal_count = 0;

   int start = StringFind(body, "\"signals\"");
   if(start < 0) return;
   start = StringFind(body, "[", start);
   if(start < 0) return;

   int pos = start;
   while(true)
   {
      int open_brace = StringFind(body, "{", pos);
      if(open_brace < 0) break;
      int close_brace = StringFind(body, "}", open_brace);
      if(close_brace < 0) break;

      string item = StringSubstr(body, open_brace, close_brace - open_brace + 1);
      HandleSignal(item);
      pos = close_brace + 1;

      // Fin du tableau : plus d'objet avant le crochet fermant.
      int next_open = StringFind(body, "{", pos);
      int array_end = StringFind(body, "]", pos);
      if(next_open < 0 || (array_end >= 0 && array_end < next_open)) break;
   }

   if(g_signal_count == 0)
      Print("Jimbot : aucun signal au-dessus du seuil (",
            MinScore > 0 ? IntegerToString(MinScore) : "seuil de l'API", ").");
}

//+------------------------------------------------------------------+
//| Mémoire des notifications déjà poussées vers le mobile            |
//+------------------------------------------------------------------+
string g_notifies[];

bool DejaNotifie(const string symbol, const string cmd)
{
   string cle = symbol + ":" + cmd;
   for(int i = 0; i < ArraySize(g_notifies); i++)
      if(g_notifies[i] == cle) return true;
   return false;
}

void RetenirNotification(const string symbol, const string cmd)
{
   string cle = symbol + ":" + cmd;
   int n = ArraySize(g_notifies);
   ArrayResize(g_notifies, n + 1);
   g_notifies[n] = cle;
}

//+------------------------------------------------------------------+
//| Traite un signal : journalise, puis exécute si autorisé          |
//+------------------------------------------------------------------+
void HandleSignal(const string item)
{
   string cmd = JsonString(item, "cmd");
   if(cmd != "BUY" && cmd != "SELL") return;

   string symbol   = ResolveSymbol(item);
   double sl       = JsonNumber(item, "sl");
   double tp       = JsonNumber(item, "tp");
   double score    = JsonNumber(item, "score");
   double rr       = JsonNumber(item, "rr");
   double exp_r    = JsonNumber(item, "expected_r");
   double risk_pct = JsonNumber(item, "risk_pct");

   g_signal_count++;
   string line = StringFormat("%s %s  score %.0f  R/R %.2f  E %.3f R",
                              symbol, cmd, score, rr, exp_r);
   ArrayResize(g_panel_lines, ArraySize(g_panel_lines) + 1);
   g_panel_lines[ArraySize(g_panel_lines) - 1] = line;
   Print("Jimbot : ", line, "  SL ", sl, "  TP ", tp);

   // Le même signal sur le téléphone. Un signal n'est notifié qu'une fois :
   // le scan réémet la même configuration à chaque passage tant qu'elle tient,
   // et sans mémoire le téléphone sonnerait toutes les cinq minutes pour la
   // même chose — ce qui revient à le faire taire au bout d'une heure.
   if(NotifyMobile && !DejaNotifie(symbol, cmd))
   {
      RetenirNotification(symbol, cmd);
      // La précision vient du signal, pas du graphique : `_Digits` est celle
      // de l'instrument sur lequel l'EA est posé, et afficher un plan DOGE
      // avec la précision de l'EURUSD arrondit 0,086140 à 0,09.
      int digits = (int)JsonNumber(item, "digits");
      if(digits <= 0) digits = _Digits;
      SendNotification(StringFormat("Jimbot %s %s — entrée %s, stop %s, objectif %s (score %.0f)",
                                    cmd, symbol,
                                    DoubleToString(JsonNumber(item, "entry"), digits),
                                    DoubleToString(sl, digits),
                                    DoubleToString(tp, digits), score));
   }

   if(!AutoTrade) return;
   if(symbol == "")
   {
      Print("Jimbot : aucun symbole correspondant chez ce courtier, signal ignoré.");
      return;
   }
   if(exp_r <= 0)
   {
      Print("Jimbot : espérance négative, signal ignoré.");
      return;
   }
   ExecuteSignal(symbol, cmd, sl, tp, MathMin(risk_pct, MaxRiskPercent));
}

//+------------------------------------------------------------------+
//| Ouvre une position dimensionnée par la distance au stop          |
//+------------------------------------------------------------------+
void ExecuteSignal(const string symbol, const string cmd,
                   const double sl, const double tp, const double risk_pct)
{
   if(PositionsForMagic() >= MaxPositions)
   {
      Print("Jimbot : ", MaxPositions, " positions déjà ouvertes, signal ignoré.");
      return;
   }
   if(HasPosition(symbol))
   {
      Print("Jimbot : position déjà ouverte sur ", symbol, ".");
      return;
   }
   if(TotalRiskPercent() + risk_pct > MaxTotalRisk)
   {
      Print("Jimbot : plafond de risque cumulé atteint (", MaxTotalRisk, "%).");
      return;
   }

   if(!SymbolSelect(symbol, true))
   {
      Print("Jimbot : symbole ", symbol, " indisponible.");
      return;
   }

   double price = (cmd == "BUY") ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                                 : SymbolInfoDouble(symbol, SYMBOL_BID);
   double lots  = LotsForRisk(symbol, price, sl, risk_pct);
   if(lots <= 0)
   {
      Print("Jimbot : volume calculé nul pour ", symbol, ", signal ignoré.");
      return;
   }

   bool ok = (cmd == "BUY")
             ? trade.Buy(lots, symbol, price, sl, tp, "jimbot")
             : trade.Sell(lots, symbol, price, sl, tp, "jimbot");

   if(ok) Print("Jimbot : ", cmd, " ", lots, " ", symbol, " exécuté.");
   else   Print("Jimbot : échec de l'ordre ", symbol, " — ", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Volume déduit du risque accepté, jamais d'un nombre de lots fixe |
//+------------------------------------------------------------------+
double LotsForRisk(const string symbol, const double entry,
                   const double sl, const double risk_pct)
{
   double distance = MathAbs(entry - sl);
   if(distance <= 0) return 0.0;

   double tick_size  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0 || tick_value <= 0) return 0.0;

   // Perte encourue par lot si le stop est touché.
   double loss_per_lot = (distance / tick_size) * tick_value;
   if(loss_per_lot <= 0) return 0.0;

   double risk_amount = AccountInfoDouble(ACCOUNT_BALANCE) * risk_pct / 100.0;
   double lots = risk_amount / loss_per_lot;

   // Alignement sur le pas de volume du courtier, en arrondissant vers le bas :
   // dépasser le risque prévu est pire que l'atteindre imparfaitement.
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double min  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(step > 0) lots = MathFloor(lots / step) * step;
   if(lots < min) return 0.0;   // le risque voulu est sous le lot minimal
   if(lots > max) lots = max;
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Retrouve le symbole du courtier parmi les alias fournis          |
//+------------------------------------------------------------------+
string ResolveSymbol(const string item)
{
   string principal = JsonString(item, "symbol");
   if(principal != "" && SymbolSelect(principal, true)) return principal;

   // Chaque courtier nomme ses instruments à sa façon : on essaie les alias.
   int start = StringFind(item, "\"aliases\"");
   if(start >= 0)
   {
      int open_bracket  = StringFind(item, "[", start);
      int close_bracket = StringFind(item, "]", open_bracket);
      if(open_bracket >= 0 && close_bracket > open_bracket)
      {
         string list = StringSubstr(item, open_bracket + 1,
                                    close_bracket - open_bracket - 1);
         string parts[];
         int n = StringSplit(list, ',', parts);
         for(int i = 0; i < n; i++)
         {
            string alias = Unquote(parts[i]);
            if(alias != "" && SymbolSelect(alias, true)) return alias;
         }
      }
   }
   return "";
}

//+------------------------------------------------------------------+
//| Comptage du risque et des positions de cet EA                    |
//+------------------------------------------------------------------+
int PositionsForMagic()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber) count++;
   }
   return count;
}

bool HasPosition(const string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber
         && PositionGetString(POSITION_SYMBOL) == symbol) return true;
   }
   return false;
}

double TotalRiskPercent()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0) return 100.0;

   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0 || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      double open   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl     = PositionGetDouble(POSITION_SL);
      if(sl <= 0) continue;   // sans stop, le risque n'est pas quantifiable

      double tick_size  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
      double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
      if(tick_size <= 0 || tick_value <= 0) continue;

      double loss = (MathAbs(open - sl) / tick_size) * tick_value
                    * PositionGetDouble(POSITION_VOLUME);
      total += loss / balance * 100.0;
   }
   return total;
}

//+------------------------------------------------------------------+
//| Panneau d'affichage                                              |
//+------------------------------------------------------------------+
void DrawPanel()
{
   ObjectsDeleteAll(0, "jimbot_");
   int y = 20;
   string header = StringFormat("JIMBOT — %d signal(s) · %s · %s",
                                g_signal_count,
                                AutoTrade ? "exécution ACTIVE" : "lecture seule",
                                TimeToString(g_last_poll, TIME_MINUTES));
   Label("jimbot_h", header, y, clrGainsboro, 10);
   y += 18;

   for(int i = 0; i < ArraySize(g_panel_lines) && i < 10; i++)
   {
      color c = (StringFind(g_panel_lines[i], " BUY ") >= 0) ? clrMediumSeaGreen
                                                             : clrIndianRed;
      Label("jimbot_" + IntegerToString(i), g_panel_lines[i], y, c, 9);
      y += 15;
   }
   if(g_signal_count == 0)
      Label("jimbot_none", "aucune configuration retenue", y, clrGray, 9);
}

void Label(const string name, const string text, const int y,
           const color clr, const int size)
{
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
//| HTTP et lecture JSON                                             |
//|                                                                  |
//| MQL5 n'a pas d'analyseur JSON. Les extracteurs ci-dessous sont   |
//| volontairement minimalistes : ils suffisent au format plat et    |
//| stable renvoyé par /api/mt, et n'ont pas vocation à traiter du   |
//| JSON quelconque.                                                 |
//+------------------------------------------------------------------+
string HttpGet(const string url)
{
   char   post[], result[];
   string headers = "Content-Type: application/json\r\n";
   string result_headers;

   ResetLastError();
   int code = WebRequest("GET", url, headers, 5000, post, result, result_headers);
   if(code == -1)
   {
      Print("Jimbot : WebRequest a échoué, erreur ", GetLastError(),
            ". Le domaine est-il autorisé dans les options ?");
      return "";
   }
   if(code != 200)
   {
      Print("Jimbot : HTTP ", code);
      return "";
   }
   return CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
}

string JsonString(const string src, const string key)
{
   int k = StringFind(src, "\"" + key + "\"");
   if(k < 0) return "";
   int colon = StringFind(src, ":", k);
   if(colon < 0) return "";
   int first = StringFind(src, "\"", colon);
   if(first < 0) return "";
   int last = StringFind(src, "\"", first + 1);
   if(last < 0) return "";
   return StringSubstr(src, first + 1, last - first - 1);
}

double JsonNumber(const string src, const string key)
{
   int k = StringFind(src, "\"" + key + "\"");
   if(k < 0) return 0.0;
   int colon = StringFind(src, ":", k);
   if(colon < 0) return 0.0;

   string buffer = "";
   for(int i = colon + 1; i < StringLen(src); i++)
   {
      ushort ch = StringGetCharacter(src, i);
      if(ch == ' ' || ch == '\n' || ch == '\r' || ch == '\t')
      {
         if(buffer == "") continue;
         break;
      }
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-' || ch == '+' || ch == 'e')
         buffer += ShortToString(ch);
      else
         break;
   }
   return (buffer == "") ? 0.0 : StringToDouble(buffer);
}

string Unquote(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   StringReplace(s, "\"", "");
   StringReplace(s, "[", "");
   StringReplace(s, "]", "");
   return s;
}
//+------------------------------------------------------------------+
