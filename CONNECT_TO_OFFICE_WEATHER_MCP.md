# Connecting to the Office Weather MCP Server

This is a demo MCP server exposing:
- **Tool**: `get_weather` — weather at our office locations
- **Resource**: `offices://locations` — list of office cities
- **Prompts**: `guardrails`, `cricket_expert`, `badminton_expert`

**Server URL (given to you separately):**
```
https://office-weather.onrender.com/mcp
```

> ⚠️ This URL only works while the server owner has it running. Free ngrok domains
> also change if the tunnel restarts — if a step below stops working, ask for the
> latest URL.

---

## 1. Check it in MCP Inspector (recommended first step)

1. Run the Inspector:
   ```bash
   npx @modelcontextprotocol/inspector
   ```
2. It opens in your browser. In the connection panel:
   - **Transport Type**: `Streamable HTTP`
   - **URL**: paste the server URL (must end in `/mcp`)
3. Click **Connect**.
4. Once connected, check the **Tools**, **Resources**, and **Prompts** tabs — you
   should see `get_weather`, `offices://locations`, and the three prompts listed.

If this works, the server is reachable and you can move on to a real client.

---

## 2. Add it in Cursor

1. Open (or create) `~/.cursor/mcp.json`.
2. Add an entry under `mcpServers`:
   ```json
   {
     "mcpServers": {
       "office-weather-demo": {
         "url": "https://office-weather.onrender.com/mcp"
       }
     }
   }
   ```
3. Reload Cursor (or use the "Reload" button in MCP settings).
4. Go to **Cursor Settings → MCP** and confirm `office-weather-demo` shows as
   connected, with `get_weather` listed under Tools.

> Note: Cursor only supports the **tools** and **resources** primitives. The
> `guardrails` / `cricket_expert` / `badminton_expert` **prompts** won't appear
> here — that's expected, use Inspector or Claude Code/Desktop to see those.

---

## 3. Add it in Claude Code

Run this in a terminal (not inside a Claude Code session):

```bash
claude mcp add --transport http office-weather-demo https://office-weather.onrender.com/mcp
```

Then start (or return to) a Claude Code session and verify:

```
/mcp
```

You should see `office-weather-demo` listed as connected. From there you can:
- Ask "what's the weather at Office 1?" → triggers the `get_weather` tool
- Reference `@office-weather-demo` → pulls in the `offices://locations` resource
- Run `/mcp__office-weather-demo__cricket_expert` → loads that persona prompt

---

## 4. Add it in Claude Desktop

1. Open Claude Desktop → **Settings → Connectors**.
2. Click **Add custom connector**.
3. Enter:
   - **Name**: `office-weather-demo`
   - **URL**: `https://office-weather.onrender.com/mcp`
4. Save, and confirm it shows as **Connected**.
5. Start a new chat and try:
   - "What's the weather at our Delhi office?"
   - Use the `+` (attach) menu to browse resources/prompts from the connector.

---

## Quick troubleshooting

- **Can't connect anywhere** → the server or ngrok tunnel may be down; check with
  the server owner.
- **Works in Inspector but not in a client** → double check the URL ends in `/mcp`
  and transport is set to Streamable HTTP (not SSE/stdio).
- **No prompts showing in Cursor** → expected, Cursor doesn't support MCP prompts.
- **"ngrok-free.app" domain looks different than before** → free-tier ngrok URLs
  change on restart; ask for the current one.
