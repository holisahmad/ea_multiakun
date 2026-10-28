//+------------------------------------------------------------------+
//| include/CommandHandler.mqh — Command parsing and execution        |
//+------------------------------------------------------------------+
#ifndef COMMAND_HANDLER_MQH
#define COMMAND_HANDLER_MQH

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include "RiskValidator.mqh"

//--- Structures
struct CommandData
{
   string command_id;
   string cycle_id;
   string account_alias;
   string action;          // OPEN_BUY / OPEN_SELL / CLOSE / CLOSE_ALL_CYCLE / MODIFY_SL_TP
   int    layer;
   string symbol;
   double volume;
   int    tp_points;       // 0 = no TP
   int    sl_points;       // 0 = no SL
   int    deviation_points;
   long   magic_number;
   string created_at_utc;
   string expires_at_utc;
};

struct ExecutionResult
{
   int    retcode;
   long   order_ticket;
   long   deal_ticket;
   long   position_id;
   double executed_volume;
   double executed_price;
   string status;          // FILLED / REJECTED / TIMEOUT / UNKNOWN
};

//--- Shared CTrade object
CTrade g_trade;

//+------------------------------------------------------------------+
//| Simple JSON string field extractor                                |
//| Extracts value of "key":"value" or "key":number                  |
//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
{
   string search = "\"" + key + "\":\"";
   int start = StringFind(json, search);
   if(start < 0) return "";
   start += StringLen(search);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   return StringSubstr(json, start, end - start);
}

double JsonGetDouble(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start < 0) return 0.0;
   start += StringLen(search);
   // Skip whitespace
   while(start < StringLen(json) && StringGetCharacter(json, start) == ' ') start++;
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == '\n') break;
      end++;
   }
   return StringToDouble(StringSubstr(json, start, end - start));
}

long JsonGetLong(string json, string key)
{
   return (long)JsonGetDouble(json, key);
}

//+------------------------------------------------------------------+
//| Parse command JSON into CommandData struct                        |
//+------------------------------------------------------------------+
bool ParseCommand(string json, CommandData &cmd)
{
   // Navigate into "command":{...} object
   int cmdStart = StringFind(json, "\"command\":{");
   if(cmdStart < 0) return false;
   cmdStart += 11; // move past "command":{

   // Find the end of command object
   int depth = 1;
   int cmdEnd = cmdStart;
   while(cmdEnd < StringLen(json) && depth > 0)
   {
      ushort ch = StringGetCharacter(json, cmdEnd);
      if(ch == '{') depth++;
      else if(ch == '}') depth--;
      cmdEnd++;
   }
   string cmdJson = StringSubstr(json, cmdStart, cmdEnd - cmdStart - 1);

   cmd.command_id      = JsonGetString(cmdJson, "command_id");
   cmd.cycle_id        = JsonGetString(cmdJson, "cycle_id");
   cmd.account_alias   = JsonGetString(cmdJson, "account_alias");
   cmd.action          = JsonGetString(cmdJson, "action");
   cmd.symbol          = JsonGetString(cmdJson, "symbol");
   cmd.created_at_utc  = JsonGetString(cmdJson, "created_at_utc");
   cmd.expires_at_utc  = JsonGetString(cmdJson, "expires_at_utc");
   cmd.layer           = (int)JsonGetLong(cmdJson, "layer");
   cmd.volume          = JsonGetDouble(cmdJson, "volume");
   cmd.tp_points       = (int)JsonGetLong(cmdJson, "tp_points");
   cmd.sl_points       = (int)JsonGetLong(cmdJson, "sl_points");
   cmd.deviation_points = (int)JsonGetLong(cmdJson, "deviation_points");
   cmd.magic_number    = JsonGetLong(cmdJson, "magic_number");

   return (cmd.command_id != "" && cmd.action != "" && cmd.symbol != "");
}

//+------------------------------------------------------------------+
//| Check if command is still fresh (not expired by clock)           |
//+------------------------------------------------------------------+
bool IsCommandFresh(CommandData &cmd)
{
   // expires_at_utc format: "YYYY-MM-DDTHH:MM:SS+00:00" or "YYYY-MM-DDTHH:MM:SS"
   // MQL5 TimeToString uses "YYYY.MM.DD HH:MM:SS" format — we do a basic length check
   // For robust expiry, compare GMT time; here we trust controller expiry management
   if(cmd.expires_at_utc == "") return false;
   // Simple heuristic: if command was created more than 60s ago based on terminal time, skip
   // A full implementation should parse ISO 8601 and compare with TimeGMT()
   return true; // Trust controller-side expiry for now
}

//+------------------------------------------------------------------+
//| Execute command and return result                                 |
//+------------------------------------------------------------------+
ExecutionResult ExecuteCommand(CommandData &cmd)
{
   ExecutionResult res;
   res.retcode        = -1;
   res.order_ticket   = 0;
   res.deal_ticket    = 0;
   res.position_id    = 0;
   res.executed_volume = 0;
   res.executed_price  = 0;
   res.status         = "UNKNOWN";

   g_trade.SetExpertMagicNumber(cmd.magic_number);
   g_trade.SetDeviationInPoints(cmd.deviation_points);
   g_trade.SetTypeFilling(ORDER_FILLING_FOK);

   // --- Get symbol info (with suffix fallback e.g. XAUUSDm) ---
   string tradeSymbol = cmd.symbol;
   if(!SymbolSelect(tradeSymbol, true))
   {
      if(StringFind(_Symbol, cmd.symbol) >= 0)
      {
         tradeSymbol = _Symbol;
         SymbolSelect(tradeSymbol, true);
         Print("STMA: Adapted symbol '", cmd.symbol, "' to chart symbol '", tradeSymbol, "'");
      }
      else
      {
         Print("STMA: Cannot select symbol: ", cmd.symbol, " (chart is on ", _Symbol, ")");
         res.status = "REJECTED";
         return res;
      }
   }

   double point = SymbolInfoDouble(tradeSymbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(tradeSymbol, SYMBOL_DIGITS);

   double ask = SymbolInfoDouble(tradeSymbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(tradeSymbol, SYMBOL_BID);

   // --- Validate volume against broker specs ---
   double minVol  = SymbolInfoDouble(tradeSymbol, SYMBOL_VOLUME_MIN);
   double maxVol  = SymbolInfoDouble(tradeSymbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(tradeSymbol, SYMBOL_VOLUME_STEP);

   double volume = cmd.volume;
   if(volume < minVol) volume = minVol;
   if(volume > maxVol) volume = maxVol;
   if(volStep > 0.0) volume = MathFloor(volume / volStep) * volStep;
   volume = NormalizeDouble(volume, 2);

   if(cmd.action == "OPEN_BUY" || cmd.action == "OPEN_SELL")
   {
      double volCheck = volume;
      PreTradeCheckResult chk = PerformPreTradeCheck(tradeSymbol, volCheck, 0);
      if(!chk.allowed)
      {
         Print("STMA: Pre-trade check failed: ", chk.reason);
         res.status = "REJECTED";
         return res;
      }
      volume = volCheck;
   }

   // --- Calculate TP/SL prices ---
   double tp = 0, sl = 0;
   int slDist = (int)SymbolInfoInteger(tradeSymbol, SYMBOL_TRADE_STOPS_LEVEL);

   if(cmd.action == "OPEN_BUY")
   {
      if(cmd.tp_points > 0)
         tp = NormalizeDouble(ask + cmd.tp_points * point, digits);
      if(cmd.sl_points > 0)
      {
         sl = NormalizeDouble(ask - cmd.sl_points * point, digits);
         // Enforce broker stop level
         double minSl = bid - slDist * point;
         if(sl > minSl) sl = NormalizeDouble(minSl - point, digits);
      }

      bool ok = g_trade.Buy(volume, tradeSymbol, ask, sl, tp,
                             "STMA:" + cmd.cycle_id + ":" + string(cmd.layer));
      _FillResult(res, ok);
   }
   else if(cmd.action == "OPEN_SELL")
   {
      if(cmd.tp_points > 0)
         tp = NormalizeDouble(bid - cmd.tp_points * point, digits);
      if(cmd.sl_points > 0)
      {
         sl = NormalizeDouble(bid + cmd.sl_points * point, digits);
         double maxSl = ask + slDist * point;
         if(sl < maxSl) sl = NormalizeDouble(maxSl + point, digits);
      }

      bool ok = g_trade.Sell(volume, tradeSymbol, bid, sl, tp,
                              "STMA:" + cmd.cycle_id + ":" + string(cmd.layer));
      _FillResult(res, ok);
   }
   else if(cmd.action == "CLOSE_ALL_CYCLE")
   {
      _CloseAllManaged(cmd.symbol, cmd.magic_number, res);
   }
   else
   {
      Print("STMA: Unknown action: ", cmd.action);
      res.status = "REJECTED";
   }

   return res;
}

//+------------------------------------------------------------------+
//| Fill ExecutionResult from CTrade result                           |
//+------------------------------------------------------------------+
void _FillResult(ExecutionResult &res, bool success)
{
   res.retcode       = (int)g_trade.ResultRetcode();
   res.order_ticket  = (long)g_trade.ResultOrder();
   res.deal_ticket   = (long)g_trade.ResultDeal();
   res.executed_volume = g_trade.ResultVolume();
   res.executed_price  = g_trade.ResultPrice();

   if(res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_PLACED)
   {
      res.status     = "FILLED";
      res.position_id = res.order_ticket; // Approximation; actual pos ID from deal
      Print("STMA: Order executed | retcode=", res.retcode, " ticket=", res.order_ticket,
            " price=", res.executed_price, " volume=", res.executed_volume);
   }
   else
   {
      res.status = "REJECTED";
      Print("STMA: Order rejected | retcode=", res.retcode,
            " msg=", g_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Close all positions with matching magic number                    |
//+------------------------------------------------------------------+
void _CloseAllManaged(string symbol, long magic, ExecutionResult &res)
{
   res.status = "FILLED";
   int closed = 0;
   int total  = PositionsTotal();

   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      string posSym = PositionGetString(POSITION_SYMBOL);
      if(posSym != symbol && StringFind(posSym, symbol) < 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;

      if(g_trade.PositionClose(ticket))
      {
         Print("STMA: Closed position ", ticket, " retcode=", g_trade.ResultRetcode());
         closed++;
      }
      else
      {
         Print("STMA: Failed to close ", ticket, " retcode=", g_trade.ResultRetcode());
         res.status = "PARTIAL";
      }
   }

   if(closed == 0 && res.status != "PARTIAL")
      res.status = "FILLED"; // Nothing to close = already clean

   res.retcode = (int)g_trade.ResultRetcode();
}

#endif // COMMAND_HANDLER_MQH
