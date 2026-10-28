//+------------------------------------------------------------------+
//|  STMA_Executor.mq5 — Smart Trade Multi Account Executor EA       |
//|  One instance per MT5 terminal / account                          |
//|  Platform: MetaTrader 5                                           |
//|  Version:  0.1.0                                                  |
//|                                                                   |
//|  IMPORTANT:                                                       |
//|  1. This EA is DEMO-ONLY until acceptance criteria are passed.    |
//|  2. Set the AccountAlias input to match config.yaml exactly.      |
//|  3. Add http://127.0.0.1:8000 to MT5 URL allowlist:              |
//|     Tools → Options → Expert Advisors → Allow WebRequest for:    |
//|  4. Never store broker passwords in this file.                    |
//+------------------------------------------------------------------+
#property copyright "STMA Project"
#property link      "https://github.com/"
#property version   "1.00"
#property strict

#include "include\HttpClient.mqh"
#include "include\CommandHandler.mqh"
#include "include\PositionManager.mqh"

//--- Input parameters
input string   AccountAlias       = "account_1";      // Must match config.yaml alias
input string   ControllerUrl      = "http://127.0.0.1:8000"; // Controller base URL
input string   AuthToken          = "CHANGE_ME_BEFORE_USE";  // Must match ea_auth_token
input int      PollIntervalMs     = 1000;              // Command poll interval (ms)
input int      StatusPushIntervalMs = 5000;            // Status push interval (ms)
input string   EA_Version         = "1.00";
input long     MagicNumber        = 20240001;          // Must match config.yaml magic_number
input bool     TrailingEnabled    = false;             // Enable trailing stop management
input int      TrailingStartPoints = 150;              // Profit points to start trailing
input int      TrailingDistancePoints = 100;           // Trailing distance in points
input int      TrailingStepPoints = 20;                // Trailing step in points

//--- Global state
ulong    g_lastStatusPushMs  = 0;
ulong    g_lastCommandPollMs = 0;
bool     g_initialized       = false;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== STMA Executor EA v", EA_Version, " ===");
   Print("Account alias: ", AccountAlias);
   Print("Controller: ",    ControllerUrl);
   Print("Account: ",       AccountInfoInteger(ACCOUNT_LOGIN));
   Print("Server: ",        AccountInfoString(ACCOUNT_SERVER));

   if(AuthToken == "CHANGE_ME_BEFORE_USE")
   {
      Alert("STMA: AuthToken is still the default placeholder. Set it in EA inputs.");
      Print("ERROR: AuthToken not configured");
      return INIT_FAILED;
   }

   // Set timer for HTTP polling (every 500ms)
   EventSetMillisecondTimer(500);

   // Immediate status push on init
   PushAccountStatus();
   PushPositions();
   g_initialized = true;

   Print("STMA Executor initialized successfully.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("STMA Executor deinitializing. Reason: ", reason);
   // Push final status with connected=false
   PushStatusDisconnected();
}

//+------------------------------------------------------------------+
//| Expert timer — reliable periodic HTTP polling & status push      |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!g_initialized) return;

   ulong now = GetTickCount();

   // Push account status periodically
   if(now - g_lastStatusPushMs >= (ulong)StatusPushIntervalMs)
   {
      PushAccountStatus();
      PushPositions();
      g_lastStatusPushMs = now;
   }

   // Poll for pending commands
   if(now - g_lastCommandPollMs >= (ulong)PollIntervalMs)
   {
      PollAndExecuteCommand();
      g_lastCommandPollMs = now;
   }
}

//+------------------------------------------------------------------+
//| Expert tick — Trailing Stop management                            |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_initialized) return;

   // Trailing stop management
   if(TrailingEnabled)
   {
      int total = PositionsTotal();
      for(int i = 0; i < total; i++)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(!PositionSelectByTicket(ticket)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

         string sym = PositionGetString(POSITION_SYMBOL);
         ManageTrailingStop(ticket, sym, TrailingStartPoints, TrailingDistancePoints, TrailingStepPoints, MagicNumber);
      }
   }
}

//+------------------------------------------------------------------+
//| Format GMT time as ISO-8601 string (YYYY-MM-DDTHH:MM:SSZ)        |
//+------------------------------------------------------------------+
string GetIsoTimestamp()
{
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

//+------------------------------------------------------------------+
//| Push account status to controller                                 |
//+------------------------------------------------------------------+
void PushAccountStatus()
{
   long   login         = AccountInfoInteger(ACCOUNT_LOGIN);
   string server        = AccountInfoString(ACCOUNT_SERVER);
   double balance       = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity        = AccountInfoDouble(ACCOUNT_EQUITY);
   double freeMargin    = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double marginLevel   = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double floatingPnl   = equity - balance;
   int    openPositions = (int)PositionsTotal();
   bool   connected     = (login > 0);

   string body = StringFormat(
      "{"
      "\"account_alias\":\"%s\","
      "\"login\":%d,"
      "\"server\":\"%s\","
      "\"balance\":%.5f,"
      "\"equity\":%.5f,"
      "\"free_margin\":%.5f,"
      "\"margin_level_pct\":%.2f,"
      "\"floating_pnl\":%.5f,"
      "\"open_positions\":%d,"
      "\"connected\":%s,"
      "\"ea_version\":\"%s\","
      "\"timestamp_utc\":\"%s\""
      "}",
      AccountAlias,
      login,
      server,
      balance,
      equity,
      freeMargin,
      marginLevel,
      floatingPnl,
      openPositions,
      connected ? "true" : "false",
      EA_Version,
      GetIsoTimestamp()
   );

   string result = HttpPost(ControllerUrl + "/ea/status", AuthToken, body);
   // Errors are logged inside HttpPost; no need to handle here unless critical
}

//+------------------------------------------------------------------+
//| Push positions to controller for reconciliation                   |
//+------------------------------------------------------------------+
void PushPositions()
{
   int total = PositionsTotal();
   string positionsJson = "[";

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;

      string symbol    = PositionGetString(POSITION_SYMBOL);
      int    type      = (int)PositionGetInteger(POSITION_TYPE);    // 0=BUY, 1=SELL
      double volume    = PositionGetDouble(POSITION_VOLUME);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double curPrice  = PositionGetDouble(POSITION_PRICE_CURRENT);
      double tp        = PositionGetDouble(POSITION_TP);
      double sl        = PositionGetDouble(POSITION_SL);
      double pnl       = PositionGetDouble(POSITION_PROFIT);
      long   magic     = PositionGetInteger(POSITION_MAGIC);
      string comment   = PositionGetString(POSITION_COMMENT);

      if(i > 0) positionsJson += ",";
      positionsJson += StringFormat(
         "{"
         "\"account_alias\":\"%s\","
         "\"position_ticket\":%I64u,"
         "\"symbol\":\"%s\","
         "\"direction\":\"%s\","
         "\"volume\":%.5f,"
         "\"open_price\":%.5f,"
         "\"current_price\":%.5f,"
         "\"tp\":%.5f,"
         "\"sl\":%.5f,"
         "\"floating_pnl\":%.5f,"
         "\"magic_number\":%d,"
         "\"comment\":\"%s\""
         "}",
         AccountAlias,
         ticket,
         symbol,
         type == POSITION_TYPE_BUY ? "BUY" : "SELL",
         volume,
         openPrice,
         curPrice,
         tp,
         sl,
         pnl,
         (int)magic,
         comment
      );
   }
   positionsJson += "]";

   string body = StringFormat(
      "{\"account_alias\":\"%s\",\"positions\":%s,\"timestamp_utc\":\"%s\"}",
      AccountAlias,
      positionsJson,
      GetIsoTimestamp()
   );

   HttpPost(ControllerUrl + "/ea/positions", AuthToken, body);
}

//+------------------------------------------------------------------+
//| Push disconnected status on deinit                                |
//+------------------------------------------------------------------+
void PushStatusDisconnected()
{
   string body = StringFormat(
      "{"
      "\"account_alias\":\"%s\","
      "\"connected\":false,"
      "\"open_positions\":0,"
      "\"ea_version\":\"%s\","
      "\"timestamp_utc\":\"%s\""
      "}",
      AccountAlias,
      EA_Version,
      GetIsoTimestamp()
   );
   HttpPost(ControllerUrl + "/ea/status", AuthToken, body);
}

//+------------------------------------------------------------------+
//| Poll controller for pending command and execute it                |
//+------------------------------------------------------------------+
void PollAndExecuteCommand()
{
   string url    = ControllerUrl + "/ea/command/" + AccountAlias;
   string result = HttpGet(url, AuthToken);

   if(result == "") return;   // Network error or timeout (already logged)

   // Parse has_command
   bool hasCommand = (StringFind(result, "\"has_command\":true") >= 0);
   if(!hasCommand) return;

   // Extract command fields
   CommandData cmd;
   if(!ParseCommand(result, cmd))
   {
      Print("STMA: Failed to parse command JSON: ", result);
      return;
   }

   // Validate command not expired
   if(!IsCommandFresh(cmd))
   {
      Print("STMA: Command ", cmd.command_id, " is expired — skipping");
      return;
   }

   // Validate account identity
   if(cmd.account_alias != AccountAlias)
   {
      Print("STMA: Command account_alias mismatch: ", cmd.account_alias, " vs ", AccountAlias);
      return;
   }

   Print("STMA: Executing command | id=", cmd.command_id, " action=", cmd.action, " volume=", cmd.volume);

   // Execute
   ExecutionResult execResult = ExecuteCommand(cmd);

   // Report result back to controller
   ReportExecutionResult(cmd.command_id, execResult);

   // Immediately reconcile positions after trade
   PushPositions();
}

//+------------------------------------------------------------------+
//| Report execution result to controller                             |
//+------------------------------------------------------------------+
void ReportExecutionResult(string commandId, ExecutionResult &res)
{
   string body = StringFormat(
      "{"
      "\"command_id\":\"%s\","
      "\"account_alias\":\"%s\","
      "\"retcode\":%d,"
      "\"order_ticket\":%I64u,"
      "\"deal_ticket\":%I64u,"
      "\"position_id\":%I64u,"
      "\"executed_volume\":%.5f,"
      "\"executed_price\":%.5f,"
      "\"status\":\"%s\","
      "\"timestamp_utc\":\"%s\""
      "}",
      commandId,
      AccountAlias,
      res.retcode,
      res.order_ticket,
      res.deal_ticket,
      res.position_id,
      res.executed_volume,
      res.executed_price,
      res.status,
      GetIsoTimestamp()
   );

   HttpPost(ControllerUrl + "/ea/execution-result", AuthToken, body);
}
//+------------------------------------------------------------------+
