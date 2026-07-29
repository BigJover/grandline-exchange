#!/usr/bin/env python3
"""
Grand Line Exchange — MCP server.

Exposes the One Piece stock-exchange data as MCP tools so an LLM
(Claude Desktop, Claude Code, etc.) can query characters, prices, market
movers and faction breakdowns.

This is a *read-only* addition to the project. It queries the same SQLite
database the site uses (grandline.db) directly, via Python's built-in
`sqlite3` module and hand-written SQL — so it has **no third-party
dependencies** and runs on the plain Python already on the machine.

It implements the MCP protocol (JSON-RPC 2.0 over newline-delimited stdio)
directly. It never writes to the database.

Run:      python3 mcp_server.py            (or ./venv/bin/python mcp_server.py)
Protocol: one JSON-RPC message per line on stdin -> one response per line on
          stdout. All diagnostics go to stderr.
"""

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Point at the site's local SQLite db by absolute path so the server works
# from any launcher cwd. Override with GLX_DB if the file lives elsewhere.
DB_PATH = os.environ.get("GLX_DB", os.path.join(HERE, "grandline.db"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "grand-line-exchange", "version": "0.1.0"}


def log(msg):
    """Diagnostics go to stderr — stdout is reserved for protocol messages."""
    print(f"[mcp] {msg}", file=sys.stderr, flush=True)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- Data helpers -----------------------------------------------------------

def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default


def _baseline_beri(price_history):
    """Earliest recorded price — used as the baseline for percent change."""
    if price_history and isinstance(price_history, list):
        first = price_history[0]
        if isinstance(first, dict) and first.get("beri"):
            return first["beri"]
    return None


def _pct_change(row, price_history):
    base = _baseline_beri(price_history)
    if base and base > 0 and row["beri"] is not None:
        return round((row["beri"] - base) / base * 100, 2)
    return None


def _summary(row, price_history=None):
    if price_history is None:
        price_history = _loads(row["price_history"], [])
    return {
        "name": row["name"],
        "faction": row["faction"] or None,
        "category": row["category"] or None,
        "beri": row["beri"],
        "pct_change_since_debut": _pct_change(row, price_history),
        "rank": (row["rank"] if "rank" in row.keys() else None) or None,
        "status": row["status"],
    }


def _full(row):
    price_history = _loads(row["price_history"], [])
    data = _summary(row, price_history)
    data.update({
        "aliases": _loads(row["aliases"], []),
        "canon_bounty": row["canon_bounty"],
        "notes": (row["notes"] or "")[:600],
        "bio": (row["bio"] or "")[:1200],
        "events": (row["events"] or "")[:1200],
        "recent_price_history": price_history[-10:],
    })
    return data


# --- Tool implementations ---------------------------------------------------

def tool_search_characters(query="", faction="", limit=10):
    """Search characters by name / alias / faction / category (case-insensitive)."""
    limit = max(1, min(int(limit), 50))
    conn = connect()
    try:
        if faction:
            rows = conn.execute(
                "SELECT * FROM characters WHERE faction LIKE ? ",
                (f"%{faction}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM characters").fetchall()
        query_l = (query or "").strip().lower()
        if query_l:
            def matches(r):
                hay = " ".join([
                    r["name"] or "", r["category"] or "",
                    " ".join(_loads(r["aliases"], [])),
                ]).lower()
                return query_l in hay
            rows = [r for r in rows if matches(r)]
        rows = sorted(rows, key=lambda r: r["beri"] or 0, reverse=True)[:limit]
        return {"count": len(rows), "results": [_summary(r) for r in rows]}
    finally:
        conn.close()


def tool_get_character(name):
    """Full profile for one character by name or alias."""
    if not name:
        return {"error": "name is required"}
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM characters WHERE name LIKE ? LIMIT 1", (name.strip(),)
        ).fetchone()
        if not row:
            name_l = name.strip().lower()
            for r in conn.execute("SELECT * FROM characters").fetchall():
                names = [r["name"] or ""] + _loads(r["aliases"], [])
                if any(name_l in (n or "").lower() for n in names):
                    row = r
                    break
        if not row:
            return {"error": f"no character matching '{name}'"}
        return _full(row)
    finally:
        conn.close()


def tool_top_movers(direction="both", limit=5):
    """Biggest gainers/losers by percent change from debut price."""
    limit = max(1, min(int(limit), 25))
    conn = connect()
    try:
        scored = []
        for r in conn.execute("SELECT * FROM characters").fetchall():
            if (r["status"] or "active") != "active":
                continue
            ph = _loads(r["price_history"], [])
            pct = _pct_change(r, ph)
            if pct is not None:
                scored.append((pct, _summary(r, ph)))
        out = {}
        if direction in ("up", "both"):
            gainers = sorted(scored, key=lambda x: x[0], reverse=True)[:limit]
            out["gainers"] = [s for _p, s in gainers]
        if direction in ("down", "both"):
            losers = sorted(scored, key=lambda x: x[0])[:limit]
            out["losers"] = [s for _p, s in losers]
        return out
    finally:
        conn.close()


def tool_market_overview():
    """Snapshot: character count, total market cap, biggest names, faction spread."""
    conn = connect()
    try:
        rows = [r for r in conn.execute("SELECT * FROM characters").fetchall()
                if (r["status"] or "active") == "active"]
        total_cap = round(sum(r["beri"] or 0 for r in rows), 2)
        top = sorted(rows, key=lambda r: r["beri"] or 0, reverse=True)[:5]
        factions = {}
        for r in rows:
            key = r["faction"] or "Unaffiliated"
            factions[key] = factions.get(key, 0) + 1
        factions = dict(sorted(factions.items(), key=lambda kv: kv[1], reverse=True))
        return {
            "active_characters": len(rows),
            "total_market_cap_beri": total_cap,
            "top_by_value": [_summary(r) for r in top],
            "characters_per_faction": factions,
        }
    finally:
        conn.close()


def tool_list_factions():
    """Every faction with its character count and average price, richest first."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(faction, ''), 'Unaffiliated') AS faction,
                   COUNT(*)     AS characters,
                   ROUND(AVG(beri), 2) AS avg_beri,
                   ROUND(SUM(beri), 2) AS total_beri
            FROM characters
            WHERE COALESCE(status, 'active') = 'active'
            GROUP BY 1
            ORDER BY total_beri DESC
            """
        ).fetchall()
        return {"factions": [dict(r) for r in rows]}
    finally:
        conn.close()


# --- Tool registry (name -> (handler, description, json-schema)) -------------

TOOLS = {
    "search_characters": (
        tool_search_characters,
        "Search the exchange for characters by name, alias, faction or category. "
        "Returns current price (beri) and change since the character's debut.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text match on name/alias/category."},
                "faction": {"type": "string", "description": "Filter by faction, e.g. 'Marines'."},
                "limit": {"type": "integer", "description": "Max results (1-50).", "default": 10},
            },
        },
    ),
    "get_character": (
        tool_get_character,
        "Get the full profile of one character: price, bounty, bio, recent price history.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Character name or alias."}},
            "required": ["name"],
        },
    ),
    "top_movers": (
        tool_top_movers,
        "List the biggest gainers and/or losers by percent change from debut price.",
        {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "both"], "default": "both"},
                "limit": {"type": "integer", "description": "How many per side (1-25).", "default": 5},
            },
        },
    ),
    "market_overview": (
        tool_market_overview,
        "Snapshot of the whole exchange: character count, total market cap, top names, faction spread.",
        {"type": "object", "properties": {}},
    ),
    "list_factions": (
        tool_list_factions,
        "Every faction with its character count, average and total price, richest first.",
        {"type": "object", "properties": {}},
    ),
}


# --- MCP / JSON-RPC plumbing -------------------------------------------------

def make_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_request(msg):
    """Return a response dict, or None for notifications (no reply expected)."""
    method = msg.get("method")
    request_id = msg.get("id")
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    if method == "initialize":
        return make_result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return make_result(request_id, {})

    if method == "tools/list":
        tools = [
            {"name": name, "description": desc, "inputSchema": schema}
            for name, (_fn, desc, schema) in TOOLS.items()
        ]
        return make_result(request_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        if not entry:
            return make_error(request_id, -32602, f"unknown tool: {name}")
        fn = entry[0]
        try:
            result = fn(**args)
            text = json.dumps(result, indent=2, default=str)
            return make_result(request_id, {"content": [{"type": "text", "text": text}]})
        except TypeError as e:
            return make_error(request_id, -32602, f"bad arguments for {name}: {e}")
        except Exception as e:
            log(f"tool {name} failed: {e}")
            return make_result(request_id, {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True,
            })

    if method == "resources/list":
        return make_result(request_id, {"resources": []})
    if method == "prompts/list":
        return make_result(request_id, {"prompts": []})

    if is_notification:
        return None
    return make_error(request_id, -32601, f"method not found: {method}")


def main():
    if not os.path.exists(DB_PATH):
        log(f"WARNING: database not found at {DB_PATH} (set GLX_DB to override)")
    log(f"grand-line-exchange MCP server up (db={DB_PATH})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"skipping non-JSON line: {e}")
            continue
        try:
            response = handle_request(msg)
        except Exception as e:
            log(f"handler crashed: {e}")
            response = make_error(msg.get("id"), -32603, f"internal error: {e}")
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
