//+------------------------------------------------------------------+
//| include/RiskValidator.mqh — Local MQL5 Pre-Trade Risk Checks     |
//+------------------------------------------------------------------+
#ifndef RISK_VALIDATOR_MQH
#define RISK_VALIDATOR_MQH

//+------------------------------------------------------------------+
//| Pre-trade validation result structure                             |
//+------------------------------------------------------------------+
struct PreTradeCheckResult
{
   bool   allowed;
   string reason;
};

//+------------------------------------------------------------------+
//| Check terminal and account trading permissions                   |
//+------------------------------------------------------------------+
bool IsTradingAllowedOnTerminal(string &reason)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      reason = "Terminal AutoTrading button is disabled";
      return false;
   }

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      reason = "EA AlgoTrading is not allowed in EA properties";
      return false;
   }

   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      reason = "Trading is not allowed for this account";
      return false;
   }

   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
   {
      reason = "Expert Advisors disabled for this account by broker";
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Check symbol trading mode and market availability                 |
//+------------------------------------------------------------------+
bool IsSymbolTradable(string symbol, string &reason)
{
   if(!SymbolInfoInteger(symbol, SYMBOL_SELECT))
   {
      if(!SymbolSelect(symbol, true))
      {
         reason = "Symbol " + symbol + " cannot be selected in MarketWatch";
         return false;
      }
   }

   long tradeMode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   if(tradeMode == SYMBOL_TRADE_MODE_DISABLED)
   {
      reason = "Trading disabled for symbol " + symbol;
      return false;
   }

   if(tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY)
   {
      reason = "Symbol " + symbol + " is in Close-Only mode";
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Check spread against max allowed points                           |
//+------------------------------------------------------------------+
bool CheckSpread(string symbol, int maxSpreadPoints, string &reason)
{
   if(maxSpreadPoints <= 0) return true; // Disabled

   int currentSpread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   if(currentSpread > maxSpreadPoints)
   {
      reason = StringFormat("Spread too high: %d points > max %d points", currentSpread, maxSpreadPoints);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Validate and normalize order volume to broker step and limits     |
//+------------------------------------------------------------------+
bool ValidateAndNormalizeVolume(string symbol, double &volume, string &reason)
{
   double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(volume < minLot)
   {
      reason = StringFormat("Requested volume %.4f < minimum broker lot %.4f", volume, minLot);
      return false;
   }

   if(volume > maxLot)
   {
      reason = StringFormat("Requested volume %.4f > maximum broker lot %.4f", volume, maxLot);
      return false;
   }

   // Normalize to step
   if(lotStep > 0.0)
   {
      volume = MathFloor(volume / lotStep) * lotStep;
      int digits = 2;
      if(lotStep < 0.1) digits = 2;
      if(lotStep < 0.01) digits = 3;
      volume = NormalizeDouble(volume, digits);
   }

   return true;
}

//+------------------------------------------------------------------+
//| Comprehensive Pre-Trade Check                                     |
//+------------------------------------------------------------------+
PreTradeCheckResult PerformPreTradeCheck(string symbol, double &volume, int maxSpreadPoints)
{
   PreTradeCheckResult res;
   res.allowed = true;
   res.reason  = "";

   string reason = "";

   if(!IsTradingAllowedOnTerminal(reason))
   {
      res.allowed = false;
      res.reason  = reason;
      return res;
   }

   if(!IsSymbolTradable(symbol, reason))
   {
      res.allowed = false;
      res.reason  = reason;
      return res;
   }

   if(!CheckSpread(symbol, maxSpreadPoints, reason))
   {
      res.allowed = false;
      res.reason  = reason;
      return res;
   }

   if(!ValidateAndNormalizeVolume(symbol, volume, reason))
   {
      res.allowed = false;
      res.reason  = reason;
      return res;
   }

   return res;
}

#endif // RISK_VALIDATOR_MQH
