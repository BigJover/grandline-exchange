# Vegapunk sentiment-pass evals

A small evals practice for the LLM pass that drives price direction:
`app.vegapunk_llm.score_chapter_sentiment` (given a chapter summary + character
list, it judges how each character's stock moves and why).

The harness has the three pieces a real evals setup needs:

1. **Golden set + direction accuracy** — `golden_set.json` holds chapter
   scenarios with the direction each character *should* move (up / down /
   neutral), derived from the events, not fan reputation. Direction match is a
   hard, programmatic assertion. `min_magnitude` additionally checks the pass
   registers decisive moments (a clean defeat) strongly enough.
2. **LLM-as-judge (groundedness)** — a cheap model (haiku) grades whether each
   `why` is actually supported by the summary and consistent with the stated
   direction. This catches "right answer, nonsense reasoning."
3. **Regression detection** — each run is compared to `baseline.json`; if any
   metric drops more than the tolerance (5%), the run prints the regression and
   exits non-zero, so it can gate CI or a pre-push hook.

## Run it

```bash
# Verify the harness with no API calls (zero cost). Uses a crude keyword model
# and a heuristic judge — proves the plumbing, NOT the real model's quality.
./venv/bin/python evals/run_evals.py --mock

# Live: evaluates the real pass. Needs the key + SDK.
pip install anthropic
export ANTHROPIC_API_KEY=...        # the same key the app uses on Railway
./venv/bin/python evals/run_evals.py

# Snapshot the current run as the regression baseline (do this once on a live run).
./venv/bin/python evals/run_evals.py --update-baseline
```

## Metrics

| Metric | Meaning |
|--------|---------|
| `direction_accuracy` | fraction of characters moved the correct way (the core signal) |
| `magnitude_ok_rate` | of the decisive cases, fraction rated at/above the expected severity |
| `grounded_rate` | fraction of `why`s the judge found grounded + consistent |
| `groundedness_mean` | mean judge score (1–5), normalised to 0–1 |

## Cost note

Live mode makes one **opus** call per case (the real pass) plus one **haiku**
call per character (the judge). The golden set is intentionally small (8 cases)
so a full live run is a handful of calls. Keep it small; grow the golden set
deliberately.

## Files

- `golden_set.json` — the cases (edit/extend this to grow coverage)
- `run_evals.py` — runner (SUT + judge + scoring + regression)
- `baseline.json` — saved metrics to regress against (committed from a `--mock`
  run as a starting point; regenerate with `--update-baseline` on a live run)
- `results.json` — last run's full per-character detail (git-ignored)

## Honest scope

This is a real, if small, evals harness — golden set, LLM-as-judge, and
regression gating over an actual production LLM pass. It is not a large
benchmark suite or a CI-integrated eval service; it's the foundation of an
evals practice, verified end-to-end in mock mode and ready to run live.
