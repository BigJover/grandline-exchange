# Chapter-pipeline admin endpoints

All live under the `/admin` prefix and require the `X-Admin-Secret` header
(checked against the `ADMIN_SECRET` env var). Base URL is the Railway prod host
or `http://localhost:8000` in dev.

```bash
GLX=https://grandline-exchange-production.up.railway.app
H="X-Admin-Secret: $ADMIN_SECRET"
```

## Detection & waves

| Method | Path | Query | Purpose |
|--------|------|-------|---------|
| POST | `/admin/chapter-detect` | `?chapter=N` (optional) | Run the detection pipeline. `chapter=N` skips Reddit detection and runs for that exact number. Idempotent; serialized by a mutex. |
| POST | `/admin/predictions/generate` | `?chapter=N&force=bool` | Generate predictions / the implications-pass wave for a chapter. `force=true` re-runs even if already generated. |
| POST | `/admin/predictions/mark-break-week` | body | Mark the current prediction as a break week. |

## Enrichment & synopsis

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/admin/chapters/{chapter_num}/enrich-youtube` | Supplement pending proposals with YouTube reaction sentiment. Needs `YOUTUBE_API_KEY`. Idempotent. |
| POST | `/admin/chapter-synopsis` (`?chapter=N`) | Publish the matured synopsis (the Saturday code path); the Vegapunk bot posts it to `#announcements` within one poll cycle (≤20m). |
| GET  | `/admin/transmission/generate` | Build the transmission content for review. |
| POST | `/admin/transmission/publish` | Publish a chapter transmission (the live TRANSMISSION dropdown content). |
| POST | `/admin/transmission/regen` | Regenerate an existing transmission. |

## Inspecting & reviewing proposals

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/admin/chapter-pulse` | Inspect the raw per-character signal scores behind the proposals. |
| POST | `/admin/proposed-prices/{id}/approve` | Apply a proposed price change to the character. |
| POST | `/admin/proposed-prices/{id}/dismiss` | Reject a price proposal. |
| POST | `/admin/proposed-characters/{id}/approve` | Add a proposed new character to the exchange. |
| POST | `/admin/proposed-characters/{id}/dismiss` | Reject a proposed new character. |

## Notes

- `chapter-detect` and `chapter-synopsis` default to the latest chapter when
  `chapter` is omitted.
- Every mutating call returns a JSON result (`detected`, `message`, `sources`,
  counts) — read it rather than assuming success.
- These are the *same* code paths the cron jobs use, exposed so an admin can run
  a phase on demand to verify it or recover from a missed/failed scheduled run.
