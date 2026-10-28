//+------------------------------------------------------------------+
//| include/PositionManager.mqh — TP/SL/Trailing stop management     |
//+------------------------------------------------------------------+
#ifndef POSITION_MANAGER_MQH
#define POSITION_MANAGER_MQH

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
// CTrade g_trade is provided globally by CommandHandler.mqh
//+------------------------------------------------------------------+
//| Manage trailing stop for a position                               |
//| Called periodically from OnTick if trailing is enabled           |
//+------------------------------------------------------------------+
bool ManageTrailingStop(
   ulong  ticket,
   string symbol,
   int    trailingStartPoints,
   int    trailingDistancePoints,
   int    trailingStepPoints,
   long   magic
)
{
   if(!PositionSelectByTicket(ticket)) return false;
   if(PositionGetInteger(POSITION_MAGIC) != magic) return false;

   double point   = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int    digits  = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   int    posType = (int)PositionGetInteger(POSITION_TYPE);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double curSL     = PositionGetDouble(POSITION_SL);

   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);

   double profitPoints;
   double newSL;

   if(posType == POSITION_TYPE_BUY)
   {
      profitPoints = (bid - openPrice) / point;
      if(profitPoints < trailingStartPoints) return false;

      newSL = NormalizeDouble(bid - trailingDistancePoints * point, digits);
      if(newSL <= curSL + trailingStepPoints * point) return false;  // Not enough movement

      // Validate stop level
      int stopLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      if(newSL >= bid - stopLevel * point) return false;

      if(g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP)))
      {
         Print("STMA Trailing: BUY ticket=", ticket, " SL moved to ", newSL);
         return true;
      }
   }
   else if(posType == POSITION_TYPE_SELL)
   {
      profitPoints = (openPrice - ask) / point;
      if(profitPoints < trailingStartPoints) return false;

      newSL = NormalizeDouble(ask + trailingDistancePoints * point, digits);
      if(curSL != 0 && newSL >= curSL - trailingStepPoints * point) return false;

      int stopLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      if(newSL <= ask + stopLevel * point) return false;

      if(g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP)))
      {
         Print("STMA Trailing: SELL ticket=", ticket, " SL moved to ", newSL);
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
//| Check if position has hit TP or SL manually (backup detection)   |
//+------------------------------------------------------------------+
bool IsPositionOpen(ulong ticket)
{
   return PositionSelectByTicket(ticket);
}

//+------------------------------------------------------------------+
//| Count managed positions for a symbol                              |
//+------------------------------------------------------------------+
int CountManagedPositions(string symbol, long magic)
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      string posSym = PositionGetString(POSITION_SYMBOL);
      if((posSym == symbol || StringFind(posSym, symbol) >= 0) &&
         PositionGetInteger(POSITION_MAGIC) == magic)
         count++;
   }
   return count;
}

#endif // POSITION_MANAGER_MQH
