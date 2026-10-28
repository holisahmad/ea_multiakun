//+------------------------------------------------------------------+
//| include/HttpClient.mqh — HTTP WebRequest wrapper for STMA EA      |
//|                                                                   |
//| Prerequisites:                                                     |
//| MT5: Tools → Options → Expert Advisors → Allow WebRequest for:   |
//|   http://127.0.0.1:8000                                           |
//+------------------------------------------------------------------+
#ifndef HTTP_CLIENT_MQH
#define HTTP_CLIENT_MQH

#define HTTP_TIMEOUT_MS 8000

//+------------------------------------------------------------------+
//| POST JSON body to a URL, return response string                   |
//+------------------------------------------------------------------+
string HttpPost(string url, string authToken, string jsonBody)
{
   string headers = "Content-Type: application/json\r\nX-Auth-Token: " + authToken + "\r\n";
   char   postData[];
   char   resultData[];
   string resultHeaders;

   int len = StringLen(jsonBody);
   StringToCharArray(jsonBody, postData, 0, len, CP_UTF8);
   if(ArraySize(postData) > len)
      ArrayResize(postData, len); // Remove null terminator

   int httpCode = WebRequest(
      "POST",
      url,
      headers,
      HTTP_TIMEOUT_MS,
      postData,
      resultData,
      resultHeaders
   );

   if(httpCode == -1)
   {
      int err = GetLastError();
      Print("STMA HttpPost error | url=", url, " err=", err,
            " (Add URL to MT5 WebRequest allowlist if err=4014)");
      return "";
   }

   if(httpCode < 200 || httpCode >= 300)
   {
      Print("STMA HttpPost non-2xx | url=", url, " code=", httpCode);
      return "";
   }

   return CharArrayToString(resultData);
}

//+------------------------------------------------------------------+
//| GET from a URL, return response string                            |
//+------------------------------------------------------------------+
string HttpGet(string url, string authToken)
{
   string headers = "X-Auth-Token: " + authToken + "\r\n";
   char   postData[];   // Empty for GET
   char   resultData[];
   string resultHeaders;

   int httpCode = WebRequest(
      "GET",
      url,
      headers,
      HTTP_TIMEOUT_MS,
      postData,
      resultData,
      resultHeaders
   );

   if(httpCode == -1)
   {
      int err = GetLastError();
      // Only log non-timeout errors to avoid log spam
      if(err != 4060) // 4060 = timeout
         Print("STMA HttpGet error | url=", url, " err=", err);
      return "";
   }

   if(httpCode < 200 || httpCode >= 300)
   {
      Print("STMA HttpGet non-2xx | url=", url, " code=", httpCode);
      return "";
   }

   return CharArrayToString(resultData);
}

#endif // HTTP_CLIENT_MQH
