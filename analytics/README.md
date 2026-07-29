# Grand Line Exchange — ClickHouse analytics

Offline OLAP analytics over the exchange's price-history events, run on the
**ClickHouse** engine (via [chDB](https://clickhouse.com/docs/en/chdb), the
embedded build of ClickHouse — same SQL dialect and functions, no server).

This is deliberately kept **separate from the live app**:

- Postgres runs the site (users, balances, trades) — that's transactional/OLTP.
- ClickHouse handles the **analytical/OLAP** side: fast aggregations over
  append-only, timestamped price events. This is the standard OLTP+OLAP split.

It's **read-only and offline** — it reads a copy of `grandline.db`, runs
everything in an in-process ClickHouse engine, and writes nothing back.

## Run it

```bash
pip install chdb          # once — ClickHouse's embedded engine
./venv/bin/python analytics/run_analytics.py
```

The runner:
1. Exports each character's JSON `price_history` from the app database.
2. Loads it into ClickHouse and flattens it with `ARRAY JOIN` + JSON functions
   into `price_points` (one row per price point — ~1,871 events).
3. Runs the queries in [`market_analytics.sql`](market_analytics.sql).

## What the queries demonstrate

| Query | ClickHouse features used |
|-------|--------------------------|
| Total market cap | `argMax(beri, chapter)` — latest value per event stream |
| Market cap by faction | `argMax` + `GROUP BY` aggregation |
| Biggest movers | `argMin`/`argMax` by chapter, computed change |
| Most volatile characters | `stddevPop`, `quantile(0.5)` |
| Activity over time | time-bucketing by chapter |
| Loading | `MergeTree` engine, `ARRAY JOIN`, `JSONExtract*` |

## Sample output (from the local snapshot)

```
Built price_points: 1871 flattened price events

Total market capitalisation
  characters = 342, total_market_cap_beri = 271,968,035,612

Market cap by faction (top)
  Blackbeard Pirates  24.50B
  Marines             21.22B
  Straw Hat Pirates   19.87B
```

## Notes

- `chdb` is only needed for this analytics job; it is **not** a dependency of
  the web app and is not in the app's `requirements.txt`.
- Point at another database with `GLX_DB=/path/to/other.db`.
