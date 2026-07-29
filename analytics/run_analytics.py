#!/usr/bin/env python3
"""
Grand Line Exchange — ClickHouse market analytics runner.

Loads the exchange's price-history events out of the app database and runs the
OLAP queries in market_analytics.sql on the ClickHouse engine (via chDB).

This is a *read-only, offline* analytics job. It does not touch the live site:
it reads a copy of grandline.db and runs everything in an in-process ClickHouse
engine. Nothing is written back.

Run:  ./venv/bin/python analytics/run_analytics.py
Deps: chdb  (pip install chdb)  — ClickHouse's embedded engine.
"""

import json
import os
import sqlite3
import sys

from chdb import session as chs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.environ.get("GLX_DB", os.path.join(ROOT, "grandline.db"))
SQL_FILE = os.path.join(HERE, "market_analytics.sql")
NDJSON = os.path.join(HERE, "_chars.ndjson")


def export_events():
    """Dump characters + their JSON price history to newline-delimited JSON."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, faction, category, price_history FROM characters "
        "WHERE price_history IS NOT NULL AND price_history != '[]'"
    ).fetchall()
    conn.close()
    with open(NDJSON, "w") as f:
        for r in rows:
            f.write(json.dumps({
                "name": r["name"] or "",
                "faction": r["faction"] or "Unaffiliated",
                "category": r["category"] or "",
                # keep the raw JSON array as a string; ClickHouse parses it
                "price_history": r["price_history"] or "[]",
            }) + "\n")
    return len(rows)


def load_statements():
    """Split the .sql file into statements, keeping each query's @title comment.

    Comments are stripped *before* splitting on ';' so that a semicolon inside a
    comment can't break a statement. '-- @title:' lines are preserved as tokens.
    """
    with open(SQL_FILE) as f:
        raw = f.read()

    cleaned = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("-- @title:"):
            cleaned.append("@@TITLE@@" + s[len("-- @title:"):].strip())
        elif s.startswith("--"):
            continue
        else:
            cleaned.append(line)

    statements = []
    for chunk in "\n".join(cleaned).split(";"):
        title = None
        code_lines = []
        for line in chunk.splitlines():
            if line.startswith("@@TITLE@@"):
                title = line[len("@@TITLE@@"):]
            else:
                code_lines.append(line)
        code = "\n".join(code_lines).strip()
        if code:
            statements.append((title, code))
    return statements


def main():
    if not os.path.exists(DB_PATH):
        sys.exit(f"database not found at {DB_PATH} (set GLX_DB to override)")

    n = export_events()
    print(f"Loaded {n} characters' price histories from {os.path.basename(DB_PATH)}\n")

    sess = chs.Session()
    # Stage the raw rows into a ClickHouse table from the NDJSON export.
    sess.query(
        "CREATE TABLE chars_raw (name String, faction String, category String, "
        "price_history String) ENGINE = Memory"
    )
    sess.query(
        f"INSERT INTO chars_raw "
        f"SELECT name, faction, category, price_history "
        f"FROM file('{NDJSON}', 'JSONEachRow', "
        f"'name String, faction String, category String, price_history String')"
    )

    points = sess.query("SELECT count() FROM chars_raw").bytes().decode().strip()
    print(f"Staged {points} characters into ClickHouse (chars_raw)\n")

    for title, code in load_statements():
        if code.upper().startswith(("CREATE", "INSERT", "DROP")):
            sess.query(code)
            if code.upper().startswith("CREATE TABLE"):
                cnt = sess.query("SELECT count() FROM price_points").bytes().decode().strip()
                print(f"Built price_points: {cnt} flattened price events\n")
            continue
        print("=" * 70)
        print(title or "(query)")
        print("=" * 70)
        print(sess.query(code, "PrettyCompact").bytes().decode())

    sess.close()
    os.remove(NDJSON)


if __name__ == "__main__":
    main()
