-- Grand Line Exchange — ClickHouse market analytics
-- =================================================
-- OLAP queries over the exchange's price-history events, run on the ClickHouse
-- engine (via chDB). The events are append-only time-series points, which is
-- exactly what ClickHouse is built for. Transactional state (users, balances)
-- stays in Postgres; this is the analytical side.
--
-- Assumes `chars_raw(name, faction, category, price_history String)` has been
-- loaded from the app database (run_analytics.py does that). price_history is a
-- JSON array of {chapter, label, beri} points.

-- Flatten each character's JSON price history into one row per price point.
-- ARRAY JOIN + JSON functions are ClickHouse doing the unnesting, not Python.
-- MergeTree with ORDER BY (character, chapter) is the columnar storage engine.
CREATE TABLE price_points
ENGINE = MergeTree
ORDER BY (character, chapter)
AS
SELECT
    name                                   AS character,
    faction,
    category,
    toUInt32(JSONExtractUInt(pt, 'chapter')) AS chapter,
    JSONExtractString(pt, 'label')         AS label,
    JSONExtractFloat(pt, 'beri')           AS beri
FROM chars_raw
ARRAY JOIN JSONExtractArrayRaw(price_history) AS pt
WHERE JSONExtractFloat(pt, 'beri') > 0;

-- @title: Total market capitalisation (latest price per character via argMax)
-- argMax(beri, chapter) picks each character's most-recent price by chapter --
-- a signature ClickHouse pattern for "latest value in an event stream".
SELECT
    count()                       AS characters,
    round(sum(latest_beri))       AS total_market_cap_beri
FROM
(
    SELECT character, argMax(beri, chapter) AS latest_beri
    FROM price_points
    GROUP BY character
);

-- @title: Market capitalisation by faction (top 10)
SELECT
    faction,
    count()                 AS characters,
    round(sum(latest_beri)) AS faction_market_cap
FROM
(
    SELECT character, faction, argMax(beri, chapter) AS latest_beri
    FROM price_points
    GROUP BY character, faction
)
GROUP BY faction
ORDER BY faction_market_cap DESC
LIMIT 10;

-- @title: Biggest movers, debut -> latest (argMin/argMax by chapter)
SELECT
    character,
    faction,
    round(argMin(beri, chapter))                                       AS debut_beri,
    round(argMax(beri, chapter))                                       AS latest_beri,
    round((argMax(beri, chapter) - argMin(beri, chapter))
          / argMin(beri, chapter) * 100, 1)                            AS pct_change
FROM price_points
GROUP BY character, faction
HAVING count() >= 2 AND argMin(beri, chapter) > 0
ORDER BY pct_change DESC
LIMIT 10;

-- @title: Most volatile characters (stddev + median over their history)
-- stddevPop and quantile are OLAP aggregate functions ClickHouse runs natively.
SELECT
    character,
    count()                        AS price_points,
    round(stddevPop(beri))         AS beri_stddev,
    round(quantile(0.5)(beri))     AS median_beri
FROM price_points
GROUP BY character
HAVING price_points >= 3
ORDER BY beri_stddev DESC
LIMIT 10;

-- @title: Market activity over time (price updates per chapter)
SELECT
    chapter,
    count()          AS price_updates,
    round(avg(beri)) AS avg_beri
FROM price_points
GROUP BY chapter
ORDER BY chapter DESC
LIMIT 15;
