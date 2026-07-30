---
name: glx-chapter-pipeline
description: >-
  Operate and supervise the Grand Line Exchange chapter pipeline — the weekly
  automation that detects a new One Piece chapter, scores character signals, and
  proposes price changes for admin review. Use this whenever the user wants to
  run or re-run chapter detection, generate/approve/dismiss price proposals,
  fire the Saturday synopsis or Monday implications pass on demand, enrich a
  chapter with YouTube sentiment, inspect chapter-pulse signal scores, or debug
  why the pipeline produced no/duplicate/odd proposals. Trigger it even when the
  user just says things like "a new chapter dropped", "run the pipeline",
  "why are there no proposals this week", "approve these price changes", or
  "regenerate the synopsis" without naming files or endpoints — this skill knows
  the weekly cycle, the cron cadence, the admin endpoints, and the review loop.
compatibility: >-
  Pipeline code in app/chapter_pipeline.py, scheduler in app/scheduler.py.
  Manual triggers are admin HTTP endpoints (POST, X-Admin-Secret header). Needs
  ANTHROPIC_API_KEY (LLM passes) and optionally YOUTUBE_API_KEY / REDDIT_CLIENT_ID
  + REDDIT_CLIENT_SECRET set on the server.
---

# GLX chapter pipeline

The pipeline turns a new One Piece chapter into **proposed** price changes that
an admin reviews and approves. It is intentionally human-in-the-loop: the
automation *proposes*, the admin *decides*. Nothing moves prices automatically
except small ambient drift.

## The weekly cycle

The whole system follows one rhythm — keep it in mind when a user asks "what
should be happening right now":

| Phase | When (UTC) | What runs | Output |
|-------|-----------|-----------|--------|
| **Chapter detection** | Fri–Mon, 09/15/21 | `detect_chapter_drop` | `ProposedPriceChange` rows (Wave 1) |
| **"Chapter changes"** | **Sat 14:00** | re-scrape → YouTube enrich → predictions → synopsis → resolution retries | matured Wave 1 + synopsis |
| **"Implication changes"** | **Mon 14:00** | `run_implications_pass` (opus LLM over wiki summary) | Wave 2 — smaller, second-order proposals |
| **Buzz / memes** | Tue–Thu (sweeps 0/8/16 daily) | `sweep_weekly_buzz` | buzz counts feeding next detection |
| Ambient drift | twice daily 06:30/18:30 | `run_market_drift` | tiny intel-free moves |
| Beri drop / volatility digest | Sun 00:00 / Sun 20:00 | income + weekly wrap | — |

Mnemonic: **chapter changes (Sat) → implication changes (Mon) → spoilers/memes
(Mon–Thu) → repeat.** Detection deliberately waits until Friday so the wiki page
matures — data quality over same-evening speed.

Detection is **idempotent** (skips chapters already processed) and guarded by a
3-hour lock, so re-running is safe.

## The core human loop: review and approve

This is the part the user does every week and the most common reason to invoke
this skill. The pipeline writes `ProposedPriceChange` rows with `status =
'pending'`; the admin approves or dismisses each one.

1. **See what's pending** — inspect the signal scores behind the proposals:
   `GET $GLX/admin/chapter-pulse` (or the admin UI's proposals panel).
2. **Approve** a proposal (applies the price change to the character):
   `POST $GLX/admin/proposed-prices/{id}/approve`
3. **Dismiss** one that's wrong:
   `POST $GLX/admin/proposed-prices/{id}/dismiss`
4. New characters the pipeline found go through the parallel
   `proposed-characters/{id}/approve|dismiss` endpoints.

When the user disagrees with a proposal, note *why* (the reason string on the
proposal shows which signals drove it) — those disagreements are the training
signal for tuning the tiers over time.

## Common manual triggers

All are POST with the admin header. Set once:

```bash
GLX=https://grandline-exchange-production.up.railway.app   # or http://localhost:8000
H="X-Admin-Secret: $ADMIN_SECRET"
```

```bash
# Run detection now. Add ?chapter=1183 to skip Reddit and force a specific chapter.
curl -X POST "$GLX/admin/chapter-detect?chapter=1183" -H "$H"

# Run the Monday implications pass on demand (second-order Wave 2 proposals).
curl -X POST "$GLX/admin/predictions/generate?chapter=1183&force=true" -H "$H"

# Publish the matured synopsis (the exact Saturday code path; bot posts ≤20m).
curl -X POST "$GLX/admin/chapter-synopsis?chapter=1183" -H "$H"

# Enrich a chapter's pending proposals with YouTube reaction sentiment.
curl -X POST "$GLX/admin/chapters/1183/enrich-youtube" -H "$H"
```

The full endpoint catalog (transmission publish/regen, prediction break-week,
etc.) is in `references/endpoints.md`.

## How proposals are scored

Detection combines multiple signals per character (wiki appearances, Reddit
comment/pulse mentions, YouTube reactions, on-site trading pressure) into a
combined rank, then maps rank → percentage tier, with sell-pressure overrides
and a mean-reversion cap. The exact tiers, signal sources, and the LLM passes
(direction/sentiment via `ANTHROPIC_API_KEY`) are documented in
`references/scoring.md`. Read that before changing tier thresholds or explaining
why a character moved the way it did.

## Troubleshooting

- **No proposals after a run.** Usually the chapter isn't on the wiki yet, or it
  was already processed (idempotent skip). Check the run's returned `message`
  and `sources`. Force it with `?chapter=N` to bypass Reddit detection.
- **Duplicate proposals.** Detection wipes + regenerates; a double-fire is
  guarded by a mutex/lock. If dupes appear, dismiss them — re-running detection
  for the same chapter is safe and won't stack.
- **"brain offline" / empty LLM output.** `ANTHROPIC_API_KEY` isn't set on the
  server — the implications/sentiment passes degrade to signal-only.
- **Reddit signals missing.** Expected without `REDDIT_CLIENT_ID/SECRET`; the
  wiki source is authoritative and the pipeline runs fine on wiki + YouTube.
- **Break weeks.** No chapter → detection correctly finds nothing. See
  `_detect_break_week`; predictions can be marked break-week via the admin API.

## Honest scope (for discussing this work)

This is a real, running weekly automation with a deliberate human approval gate
and multi-source signal fusion. It is not an ML model — it's rule-based tiers
plus LLM passes for direction/sentiment. Say it that way.
