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


def is_available() -> bool:
    """Returns True if the Anthropic API key is configured and the package is installed."""
    return _get_client() is not None
