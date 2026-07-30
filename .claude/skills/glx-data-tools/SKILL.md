---
name: glx-data-tools
description: >-
  Run and reason about the Grand Line Exchange's data tooling — the read-only
  MCP server (mcp_server.py) and the ClickHouse OLAP analytics
  (analytics/run_analytics.py + market_analytics.sql). Use this whenever the
  user wants to query exchange/character/price data, get a market snapshot,
  find top movers or the biggest/most-volatile characters, compute market cap
  or per-faction totals, run the analytics job, expose GLX data to an LLM via
  MCP, or extend either tool with new tools/queries. Trigger it even when the
  user just says things like "what are the movers", "how big is the market",
  "add a tool to the MCP server", or "add a ClickHouse query" without naming
  the files — this skill knows where they live and how they work.
compatibility: >-
  Read-only against a local grandline.db (SQLite). The analytics job needs
  `pip install chdb` (ClickHouse's embedded engine). Both run on plain Python;
  no server, no site changes.
---

# Grand Line Exchange — data tools

Two standalone, **read-only** tools sit beside the GLX web app (they never
import it or write to its database):

1. `mcp_server.py` — an **MCP server** exposing exchange data as LLM-callable
   tools over JSON-RPC/stdio. Use for point lookups and live questions.
2. `analytics/` — a **ClickHouse OLAP** job over the price-history event stream.
   Use for aggregations, market cap, movers-at-scale, volatility, trends.

Pick the MCP server for "answer a question about a character/the market right
now." Pick the analytics job for "aggregate/analyze the whole event history."

The data source is a local `grandline.db` (SQLite) at the repo root. Override
with the `GLX_DB` environment variable. Neither tool touches the live site, so
they're safe to run and iterate on freely.

## MCP server (`mcp_server.py`)

Five tools: `search_characters`, `get_character`, `top_movers`,
`market_overview`, `list_factions`. It speaks MCP over stdio — one JSON-RPC
message per line on stdin, one response per line on stdout; logs go to stderr.

**Answer a quick question without a client** by piping a handshake + call:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"top_movers","arguments":{"limit":5}}}' \
  | python3 mcp_server.py
```

The tool result is JSON inside `result.content[0].text`. Swap the `name` and
`arguments` for other tools (see `tools/list` for the exact input schemas).

**Wire it into a client** so a human can ask in natural language:

```bash
claude mcp add grand-line-exchange -- python3 /absolute/path/to/mcp_server.py
```

See `MCP_SERVER.md` at the repo root for the Claude Desktop config block and
the full tool table.

**Extending it:** add a `tool_<name>(...)` function that opens a session with
`connect()`, runs read-only SQL, and returns a JSON-able dict; then register it
in the `TOOLS` dict with a description and a JSON-Schema for its arguments. Keep
it read-only — the value of these tools is that they're safe to point at data.

## ClickHouse analytics (`analytics/`)

Run the whole OLAP pipeline:

```bash
pip install chdb            # once — ClickHouse's embedded engine
python3 analytics/run_analytics.py
```

`run_analytics.py` exports each character's JSON `price_history` from the app
DB, loads it into ClickHouse, flattens it into a `price_points` MergeTree table
(~1,871 events), then runs the queries in `analytics/market_analytics.sql`:
total market cap, cap by faction, biggest movers, most volatile characters, and
activity over time.

`chdb` is only a dependency of this job — deliberately **not** in the app's
`requirements.txt`, so the site's deploy is unaffected.

### The ClickHouse patterns that matter

These are the idioms worth understanding before editing the SQL — they're what
make this "OLAP", not just SQL-in-a-different-engine. Full worked explanations
are in `references/clickhouse-patterns.md`; the short version:

- **Flatten JSON events with `ARRAY JOIN` + `JSONExtract*`.** The price history
  lives as a JSON array per character. `ARRAY JOIN JSONExtractArrayRaw(...)`
  explodes it into one row per point, and `JSONExtractUInt/Float/String` pull
  typed fields. This is ClickHouse doing the unnesting, so the analytics stay
  in the engine rather than being pre-chewed in Python.
- **Latest-value-per-stream with `argMax(value, ordering)`.** `argMax(beri,
  chapter)` returns each character's most recent price. Summing that across
  characters gives market cap "as of now" — the canonical way to collapse an
  append-only event stream to a current snapshot without a subquery join.
- **Distribution with `stddevPop` / `quantile`.** Volatility and medians come
  from native aggregate functions, not hand-rolled math — the point of a
  columnar OLAP store is that these run fast over the whole column.

When adding a query, append it to `market_analytics.sql` with a
`-- @title: ...` comment on the line above — the runner uses that as the
printed heading, so titled queries show up automatically with no code change.

## Honest scope (useful when discussing this work)

- Both tools are **local and read-only**; the analytics uses **chDB**, the
  *embedded* ClickHouse engine, not a standalone server or cluster. Same SQL
  dialect and functions — but it isn't production ClickHouse ops at scale.
- The app itself runs on **Postgres** (transactional side). ClickHouse here is
  the analytical/OLAP counterpart — the standard OLTP + OLAP split.
