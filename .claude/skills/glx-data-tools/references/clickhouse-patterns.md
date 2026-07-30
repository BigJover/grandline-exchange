# ClickHouse patterns used in GLX analytics

Worked explanations of the idioms in `analytics/market_analytics.sql`. Read
this when editing the queries or explaining why they're written this way.

## 1. Flattening a JSON event array with ARRAY JOIN

Each character row stores its whole price history as a JSON array string:

```json
[{"chapter":900,"label":"WCI End","beri":200000000},
 {"chapter":1000,"label":"Wano Peak","beri":2000000000}]
```

To analyze it we want one row *per price point*, not per character. `ARRAY JOIN`
over `JSONExtractArrayRaw` explodes the array; `JSONExtract*` pulls typed fields:

```sql
SELECT
    name                                     AS character,
    toUInt32(JSONExtractUInt(pt, 'chapter')) AS chapter,
    JSONExtractString(pt, 'label')           AS label,
    JSONExtractFloat(pt, 'beri')             AS beri
FROM chars_raw
ARRAY JOIN JSONExtractArrayRaw(price_history) AS pt
```

`JSONExtractArrayRaw` returns each element as a raw JSON string (`pt`), which
the per-field `JSONExtract*` calls then parse. Doing this in the engine (rather
than pre-flattening in Python) is the point: the analytics live in ClickHouse.

The result is stored as a `MergeTree` table ordered by `(character, chapter)` —
MergeTree is ClickHouse's columnar storage engine, and the ORDER BY is the
sort/primary key that makes range scans and grouping cheap.

## 2. Latest-value-per-stream with argMax

An append-only event stream has many rows per character. To get the *current*
price you want the `beri` from the row with the highest `chapter`:

```sql
SELECT character, argMax(beri, chapter) AS latest_beri
FROM price_points
GROUP BY character
```

`argMax(value, ordering)` returns `value` from the row where `ordering` is
maximal — no self-join, no window function needed. Summing `latest_beri` across
all characters is the market cap "as of now". `argMin(beri, chapter)` is the
mirror image (debut price), so a single grouped query yields debut → latest and
the percent change between them.

## 3. Distribution and volatility with native aggregates

```sql
SELECT
    character,
    stddevPop(beri)        AS beri_stddev,
    quantile(0.5)(beri)    AS median_beri
FROM price_points
GROUP BY character
HAVING count() >= 3
```

`stddevPop` (population standard deviation) and `quantile(p)(col)` are built-in
aggregate functions. The reason to reach for a columnar OLAP store is that these
run fast over an entire column; you don't hand-roll the math or pull rows into
the app to compute them.

## 4. Time-bucketing

Grouping by `chapter` gives a time axis (chapters are the exchange's clock):

```sql
SELECT chapter, count() AS price_updates, round(avg(beri)) AS avg_beri
FROM price_points
GROUP BY chapter
ORDER BY chapter DESC
```

For finer control, `toStartOfInterval` / `toStartOf*` functions bucket true
timestamps — not needed here because chapters already are the natural bucket.

## Adding a query

Append it to `market_analytics.sql` with a heading comment:

```sql
-- @title: My new metric
SELECT ...;
```

`run_analytics.py` splits the file on `;` (after stripping comments) and prints
each `SELECT`/`WITH` result under its `@title`. DDL (`CREATE`/`INSERT`) runs
silently. No runner changes are needed to add a titled query.
