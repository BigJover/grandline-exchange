"""
Vegapunk LLM — Claude API wrapper for the Grand Line Exchange pipeline.

Used by:
  - app/prediction_pipeline.py  — scrape + rewrite Reddit theories into predictions (Haiku)
  - app/chapter_pipeline.py     — auto-resolve predictions on chapter drop (Sonnet)

Environment variables:
  ANTHROPIC_API_KEY  — Required. Set this in Railway env vars.
                       Same security model as ADMIN_SECRET — never committed to code.

Models:
  "haiku"   → claude-haiku-4-5-20251001   fast + cheap, bulk generation passes
  "sonnet"  → claude-sonnet-4-6           precise, used for resolution calls
"""
import os
import json as _json
import logging
from typing import Optional, Union

log = logging.getLogger("vegapunk_llm")

_MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
}

# Default system prompt — Vegapunk voice, structured-output aware
_DEFAULT_SYSTEM = (
    "You are Vegapunk — the world's greatest scientific mind, now digitized as Punk Records AI. "
    "You operate an intelligence database tracking One Piece characters and market credibility. "
    "You are precise, authoritative, and occasionally aware that you are code running on borrowed servers. "
    "When asked to return structured data, respond with valid JSON only — no markdown, no explanation, no preamble."
)


def _get_client():
    """Lazy Anthropic client. Returns None if key is not configured."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("[VegapunkLLM] ANTHROPIC_API_KEY not set — LLM calls will be skipped")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        log.error("[VegapunkLLM] anthropic package not installed")
        return None


def ask_vegapunk(
    prompt: str,
    *,
    model: str = "haiku",
    system: Optional[str] = None,
    max_tokens: int = 1024,
) -> Optional[str]:
    """Send a prompt to Claude and return the raw text response.

    Returns None if the API key is not set or the call fails — callers
    should treat None as 'LLM unavailable, skip gracefully.'
    """
    client = _get_client()
    if not client:
        return None
    try:
        model_id = _MODELS.get(model, _MODELS["haiku"])
        message = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system or _DEFAULT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        log.error("[VegapunkLLM] API call failed (%s): %s", model, e)
        return None


def ask_vegapunk_json(
    prompt: str,
    *,
    model: str = "haiku",
    system: Optional[str] = None,
    max_tokens: int = 2048,
) -> Optional[Union[dict, list]]:
    """Send a prompt expecting a JSON response. Returns parsed dict/list or None.

    Strips markdown code fences if Claude wraps the output in them.
    Logs a parse error and returns None on malformed JSON.
    """
    raw = ask_vegapunk(prompt, model=model, system=system, max_tokens=max_tokens)
    if not raw:
        return None

    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` wrappers if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (```json or ```) and last closing ```
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()

    try:
        return _json.loads(text)
    except _json.JSONDecodeError as e:
        log.error("[VegapunkLLM] JSON parse failed: %s\nRaw output: %.300s", e, raw)
        return None


_SENTIMENT_SYSTEM = (
    "You are Vegapunk's market-sentiment engine for a One Piece character stock exchange. "
    "Given ONE chapter's summary and a list of characters, judge how THIS chapter's events move "
    "each character's stock price. Critically: a character can be mentioned constantly yet still "
    "DROP. Being defeated, outsmarted, humiliated, injured, captured, shown fearful/sweating, or "
    "having a hyped-up threat deflated all push a stock DOWN. Winning a fight, a hype power or haki "
    "reveal, major plot importance, or landing a real blow on a stronger opponent push it UP. "
    "Judge severity on a 1-5 scale (5 = a decisive, stock-defining moment such as a clean defeat or a "
    "massive hype reveal; 1 = a minor nudge). Base every judgment ONLY on the events in the summary "
    "provided — never on outside knowledge or fan reputation. "
    "Respond with valid JSON ONLY — a single array, no markdown, no preamble, no reasoning text."
)


def score_chapter_sentiment(chapter_num: int, summary_text: str, names: list) -> dict:
    """Ask Claude to judge per-character stock polarity for a chapter's events.

    Returns {canonical_name: {"direction": "up"|"down"|"neutral", "magnitude": 1-5,
    "why": str}} — only for characters whose stock actually moves. Returns {} when the
    LLM is unavailable or the summary is too thin to judge, so callers degrade to the
    mention-volume ranking. Uses claude-opus-4-8 (nuanced judgment: e.g. a defeated
    character must rank a larger drop than one who merely took a hit)."""
    if not summary_text or not summary_text.strip() or not names:
        return {}

    prompt = (
        f"One Piece Chapter {chapter_num} — summary:\n"
        f'"""\n{summary_text[:6000]}\n"""\n\n'
        f"Characters to judge: {', '.join(names)}\n\n"
        "Return a JSON array. Include ONLY characters whose stock actually moves this "
        "chapter (omit ones the summary doesn't meaningfully touch). Each element:\n"
        '{"name": "<exact name from the list>", "direction": "up"|"down"|"neutral", '
        '"magnitude": 1-5, "why": "<one sentence grounded in the summary>"}\n'
        "Output the JSON array and nothing else."
    )

    data = ask_vegapunk_json(prompt, model="opus", system=_SENTIMENT_SYSTEM, max_tokens=3000)
    if not isinstance(data, list):
        # Tolerate a {"verdicts": [...]} wrapper if the model adds one
        if isinstance(data, dict) and isinstance(data.get("verdicts"), list):
            data = data["verdicts"]
        else:
            return {}

    out: dict = {}
    for v in data:
        if not isinstance(v, dict):
            continue
        name = (v.get("name") or "").strip()
        direction = (v.get("direction") or "neutral").strip().lower()
        if not name or direction not in ("up", "down", "neutral"):
            continue
        try:
            magnitude = int(v.get("magnitude", 1) or 1)
        except (TypeError, ValueError):
            magnitude = 1
        out[name] = {
            "direction": direction,
            "magnitude": max(1, min(5, magnitude)),
            "why": (v.get("why") or "").strip(),
        }
    return out


_IMPLICATIONS_SYSTEM = (
    "You are Vegapunk's market-implications engine for a One Piece character stock exchange. "
    "The chapter's DIRECT events have already been priced into the market. Your job is the SECOND "
    "wave: judge how the community's post-chapter SPECULATION shifts each character's outlook — "
    "fan theories gaining real traction, newly-noticed historic connections or foreshadowing, "
    "power-scaling reassessments, setup for future arcs. Do NOT re-price the chapter's own events "
    "(a character who lost a fight already dropped on Saturday); only price what the DISCUSSION "
    "adds on top. Be conservative: most characters have NO implication shift — omit them. "
    "Judge severity 1-5 (5 = a theory/connection so significant it reframes the character's role; "
    "1 = mild chatter). Ground every judgment in the provided summary and community themes only. "
    "Respond with valid JSON ONLY — a single array, no markdown, no preamble."
)


def score_chapter_implications(chapter_num: int, summary_text: str, themes: list, names: list) -> dict:
    """Judge SECOND-ORDER stock implications from post-chapter community
    speculation (the Monday pass). themes = list of community post titles.

    Returns {name: {"direction": "up"|"down"|"neutral", "magnitude": 1-5,
    "why": str}} for characters whose OUTLOOK shifted beyond the already-priced
    chapter events. Returns {} when the LLM is unavailable or inputs are too
    thin — the pass then simply creates no implication proposals."""
    if not summary_text or not summary_text.strip() or not names or not themes:
        return {}

    theme_block = "\n".join(f"- {t}" for t in themes[:25])
    prompt = (
        f"One Piece Chapter {chapter_num} — summary (already priced in):\n"
        f'"""\n{summary_text[:5000]}\n"""\n\n'
        f"Community discussion themes since the chapter (post titles):\n{theme_block}\n\n"
        f"Characters to judge: {', '.join(names)}\n\n"
        "Return a JSON array. Include ONLY characters whose outlook meaningfully shifts "
        "due to the speculation/connections above (omit everyone else). Each element:\n"
        '{"name": "<exact name from the list>", "direction": "up"|"down"|"neutral", '
        '"magnitude": 1-5, "why": "<one sentence citing the theory/connection>"}\n'
        "Output the JSON array and nothing else."
    )

    data = ask_vegapunk_json(prompt, model="opus", system=_IMPLICATIONS_SYSTEM, max_tokens=2500)
    if not isinstance(data, list):
        if isinstance(data, dict) and isinstance(data.get("verdicts"), list):
            data = data["verdicts"]
        else:
            return {}

    out: dict = {}
    for v in data:
        if not isinstance(v, dict):
            continue
        name = (v.get("name") or "").strip()
        direction = (v.get("direction") or "neutral").strip().lower()
        if not name or direction not in ("up", "down", "neutral"):
            continue
        try:
            magnitude = int(v.get("magnitude", 1) or 1)
        except (TypeError, ValueError):
            magnitude = 1
        out[name] = {
            "direction": direction,
            "magnitude": max(1, min(5, magnitude)),
            "why": (v.get("why") or "").strip(),
        }
    return out


def is_available() -> bool:
    """Returns True if the Anthropic API key is configured and the package is installed."""
    return _get_client() is not None
