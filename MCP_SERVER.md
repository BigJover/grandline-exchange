# Grand Line Exchange — MCP server

`mcp_server.py` exposes the exchange's data to any MCP client (Claude Desktop,
Claude Code, etc.) as callable tools. It is a **read-only, zero-dependency
addition** — it queries the same `grandline.db` the site uses via Python's
built-in `sqlite3` and never writes. It does not touch or import the web app,
so it cannot affect the running site.

## Tools

| Tool | What it does |
|------|--------------|
| `search_characters` | Find characters by name / alias / faction / category |
| `get_character` | Full profile: price, bounty, aliases, bio, recent price history |
| `top_movers` | Biggest gainers / losers by % change from debut price |
| `market_overview` | Character count, total market cap, top names, faction spread |
| `list_factions` | Every faction with count, average and total price |

## Run it directly

```bash
python3 mcp_server.py
```

It speaks JSON-RPC 2.0 over stdio (one message per line). Quick smoke test:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"market_overview","arguments":{}}}' \
  | python3 mcp_server.py
```

## Wire it into Claude Desktop / Claude Code

Add to your MCP config (Claude Desktop:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "grand-line-exchange": {
      "command": "python3",
      "args": ["/Users/jovankirovski/grandline-exchange/mcp_server.py"]
    }
  }
}
```

For Claude Code: `claude mcp add grand-line-exchange -- python3 /Users/jovankirovski/grandline-exchange/mcp_server.py`

Then ask e.g. *"Which characters are the biggest movers on the exchange?"* and
the model will call `top_movers`.

## Notes

- Point at a different database with `GLX_DB=/path/to/other.db`.
- The server reads the local SQLite snapshot; it does not require the site's
  virtualenv or any of its packages.
