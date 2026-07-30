#!/usr/bin/env python3
"""
Vegapunk sentiment-pass eval harness.

Evaluates `app.vegapunk_llm.score_chapter_sentiment` — the LLM pass that judges
how a chapter's events move each character's stock — against a golden set, with
three layers the JD asks for:

  1. Golden set + direction accuracy  — the hard, programmatic assertion:
     did the pass move each character the right way (up/down/neutral)?
  2. LLM-as-judge (groundedness)      — a cheap model grades whether each
     "why" is actually supported by the summary and consistent with the call.
  3. Regression detection             — compares this run to a saved baseline
     and exits non-zero if any metric drops beyond tolerance (CI-gate friendly).

Modes:
  --mock              Run without any API calls, using a crude keyword model and
                      a heuristic judge. Verifies the harness plumbing end-to-end
                      at zero cost. NOT a measure of the real model's quality.
  (default = live)    Calls the real Vegapunk pass (needs ANTHROPIC_API_KEY and
                      `pip install anthropic`). Uses opus for the pass (as prod
                      does) and haiku for the judge (cheap).

Usage:
  ./venv/bin/python evals/run_evals.py --mock
  ./venv/bin/python evals/run_evals.py                 # live
  ./venv/bin/python evals/run_evals.py --update-baseline   # save current as baseline
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

GOLDEN = os.path.join(HERE, "golden_set.json")
BASELINE = os.path.join(HERE, "baseline.json")
RESULTS = os.path.join(HERE, "results.json")

TOLERANCE = 0.05  # a metric may dip this much before it's called a regression

# metrics where higher is better; used by the regression check
TRACKED_METRICS = [
    "direction_accuracy",
    "magnitude_ok_rate",
    "grounded_rate",
    "groundedness_mean",
]

_UP_KW = ["victorious", "unveils", "cleanly", "stunned", "respect", "staggers",
          "descended", "central", "importance", "blow", "reveal", "new"]
_DOWN_KW = ["defeated", "collapses", "struck", "outsmarted", "flees", "knocked",
            "brushed", "captured", "cuffs", "locked", "overpowered", "humiliat",
            "ignored", "panic", "hollow", "ambushed", "dragged"]


# --- Mock model + judge (for --mock; zero API cost) -------------------------

def mock_score(chapter, summary, names):
    """Crude per-character keyword model. Exists to exercise the harness without
    the API — it is deliberately simple and is not a real quality signal."""
    sentences = [s.strip() for s in summary.replace("\n", " ").split(".") if s.strip()]
    out = {}
    for name in names:
        relevant = [s for s in sentences if name.lower() in s.lower()] or sentences
        text = " ".join(relevant).lower()
        up = sum(text.count(k) for k in _UP_KW)
        down = sum(text.count(k) for k in _DOWN_KW)
        if up > down:
            direction, mag = "up", min(5, 2 + (up - down))
        elif down > up:
            direction, mag = "down", min(5, 2 + (down - up))
        else:
            direction, mag = "neutral", 1
        out[name] = {"direction": direction, "magnitude": mag,
                     "why": f"mock: up={up} down={down} in relevant text"}
    return out


def mock_judge(summary, name, verdict):
    """Heuristic stand-in for the LLM judge: a 'why' that isn't empty and doesn't
    contradict a strong call is treated as grounded."""
    why = (verdict.get("why") or "").strip()
    grounded = bool(why)
    return {"grounded": grounded, "score": 4 if grounded else 1,
            "note": "heuristic judge (mock mode)"}


# --- Live judge (LLM-as-judge) ----------------------------------------------

_JUDGE_SYSTEM = (
    "You are a strict evaluator of a market-sentiment model for a fiction stock "
    "exchange. You are given a chapter summary, a character, and the model's call "
    "(direction + one-sentence reason). Judge ONLY whether the reason is actually "
    "supported by events in the summary and is consistent with the stated "
    "direction. Do not judge whether you personally agree with the direction — "
    "only whether the reason is grounded in the summary and self-consistent. "
    "Respond with JSON only: "
    '{"grounded": true|false, "score": 1-5, "note": "<short reason>"}'
)


def llm_judge(summary, name, verdict):
    from app.vegapunk_llm import ask_vegapunk_json
    prompt = (
        f"Chapter summary:\n\"\"\"\n{summary[:4000]}\n\"\"\"\n\n"
        f"Character: {name}\n"
        f"Model call: direction={verdict.get('direction')}, "
        f"reason=\"{verdict.get('why')}\"\n\n"
        "Is the reason grounded in the summary and consistent with the direction? "
        "Return the JSON object only."
    )
    res = ask_vegapunk_json(prompt, model="haiku", system=_JUDGE_SYSTEM, max_tokens=300)
    if not isinstance(res, dict):
        return {"grounded": False, "score": 1, "note": "judge returned no/invalid JSON"}
    try:
        score = max(1, min(5, int(res.get("score", 1))))
    except (TypeError, ValueError):
        score = 1
    return {"grounded": bool(res.get("grounded")), "score": score,
            "note": (res.get("note") or "").strip()[:160]}


# --- Eval core --------------------------------------------------------------

def run(mock: bool):
    with open(GOLDEN) as f:
        golden = json.load(f)

    if mock:
        score_fn, judge_fn, mode = mock_score, mock_judge, "mock"
    else:
        from app.vegapunk_llm import score_chapter_sentiment, is_available
        if not is_available():
            sys.exit("Live mode needs ANTHROPIC_API_KEY set and `pip install anthropic`. "
                     "Use --mock to verify the harness without the API.")
        score_fn, judge_fn, mode = score_chapter_sentiment, llm_judge, "live"

    rows = []
    for case in golden["cases"]:
        verdicts = score_fn(case["chapter"], case["summary"], case["characters"])
        for name, exp in case["expect"].items():
            got = verdicts.get(name, {"direction": "neutral", "magnitude": 1, "why": ""})
            dir_ok = got["direction"] == exp["direction"]
            # magnitude only matters when we expected a decisive move and got the direction right
            min_mag = exp.get("min_magnitude")
            mag_ok = (min_mag is None) or (dir_ok and got.get("magnitude", 0) >= min_mag)
            judged = judge_fn(case["summary"], name, got)
            rows.append({
                "case": case["id"], "character": name,
                "expected": exp["direction"], "got": got["direction"],
                "magnitude": got.get("magnitude"), "min_magnitude": min_mag,
                "direction_ok": dir_ok, "magnitude_ok": mag_ok,
                "grounded": judged["grounded"], "judge_score": judged["score"],
                "why": got.get("why", ""), "judge_note": judged["note"],
            })

    n = len(rows)
    mag_rows = [r for r in rows if r["min_magnitude"] is not None]
    metrics = {
        "mode": mode,
        "cases": len(golden["cases"]),
        "characters_judged": n,
        "direction_accuracy": round(sum(r["direction_ok"] for r in rows) / n, 4),
        "magnitude_ok_rate": round(
            sum(r["magnitude_ok"] for r in mag_rows) / len(mag_rows), 4) if mag_rows else 1.0,
        "grounded_rate": round(sum(r["grounded"] for r in rows) / n, 4),
        "groundedness_mean": round(sum(r["judge_score"] for r in rows) / n / 5, 4),
    }
    return metrics, rows


def compare_to_baseline(current, baseline, tol=TOLERANCE):
    """Return a list of (metric, baseline, current) tuples that regressed."""
    regressions = []
    for m in TRACKED_METRICS:
        if m in baseline and current.get(m, 0) < baseline[m] - tol:
            regressions.append((m, baseline[m], current[m]))
    return regressions


def print_report(metrics, rows, regressions):
    print(f"\n  Vegapunk sentiment eval — {metrics['mode']} mode")
    print("  " + "-" * 74)
    print(f"  {'case':<30}{'char':<16}{'exp':<9}{'got':<9}{'dir':<5}{'grnd':<5}")
    print("  " + "-" * 74)
    for r in rows:
        d = "✓" if r["direction_ok"] else "✗"
        g = "✓" if r["grounded"] else "·"
        print(f"  {r['case']:<30}{r['character'][:15]:<16}{r['expected']:<9}"
              f"{r['got']:<9}{d:<5}{g:<5}")
    print("  " + "-" * 74)
    print(f"  direction_accuracy : {metrics['direction_accuracy']:.0%}")
    print(f"  magnitude_ok_rate  : {metrics['magnitude_ok_rate']:.0%}")
    print(f"  grounded_rate      : {metrics['grounded_rate']:.0%}  (LLM-as-judge)")
    print(f"  groundedness_mean  : {metrics['groundedness_mean']:.0%}  (judge score /5)")
    if regressions:
        print("\n  ⚠ REGRESSION vs baseline:")
        for m, base, cur in regressions:
            print(f"    {m}: {base:.0%} → {cur:.0%}")
    else:
        print("\n  ✓ no regression vs baseline")
    print()


def main():
    ap = argparse.ArgumentParser(description="Vegapunk sentiment eval harness")
    ap.add_argument("--mock", action="store_true", help="run without API calls")
    ap.add_argument("--update-baseline", action="store_true",
                    help="save this run's metrics as the regression baseline")
    args = ap.parse_args()

    metrics, rows = run(mock=args.mock)

    baseline = {}
    if os.path.exists(BASELINE):
        with open(BASELINE) as f:
            baseline = json.load(f)
    regressions = compare_to_baseline(metrics, baseline) if baseline else []

    print_report(metrics, rows, regressions)

    with open(RESULTS, "w") as f:
        json.dump({"metrics": metrics, "rows": rows}, f, indent=2)

    if args.update_baseline:
        with open(BASELINE, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"  baseline updated → {os.path.relpath(BASELINE, ROOT)}\n")
        return

    # Non-zero exit on regression so this can gate CI / a pre-push hook.
    sys.exit(1 if regressions else 0)


if __name__ == "__main__":
    main()
