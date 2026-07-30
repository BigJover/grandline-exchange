# How the pipeline scores characters and sets prices

Read this before changing tier thresholds or explaining why a character moved.
Source: `app/chapter_pipeline.py`.

## Signal sources (fused per character)

`detect_chapter_drop` combines several independent signals into one score:

1. **Fandom wiki** — authoritative, no auth, always works. Chapter appearances
   (`_wiki_chapter_chars`), newly introduced names (`_wiki_chapter_new_names`),
   and the chapter summary (`_wiki_chapter_summary`) used by the LLM passes.
2. **Reddit** — `/new`, `/hot`, `/search` plus top-comment character mentions
   (`_reddit_comment_chars`) and a pulse score (`_reddit_pulse_chars`).
   Supplementary; needs `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` and may be
   blocked on Railway. The pipeline runs fine without it.
3. **YouTube** — reaction-video comment mentions (`_youtube_comment_chars`,
   applied via `enrich_chapter_with_youtube`). Needs `YOUTUBE_API_KEY`.
4. **On-site trading pressure** — recent net buy/sell activity per character.
5. **Weekly buzz** — rolling chatter counts (`sweep_weekly_buzz` /
   `weekly_buzz_counts`) that carry momentum into the next detection.

`_combined_total` merges these; `_build_reason` renders the human-readable
reason string attached to each proposal (this is what to read when deciding
whether to approve).

## Proposal tiers (rank → percentage)

`_proposal_tier` maps a character's combined-signal rank to a price change:

| Rank | Change |
|------|--------|
| #1 | +7 % (subject to mean-reversion cap) |
| #2 | +5 % |
| #3–4 | +3.5 % |
| #5–7 | +2 % |
| #8–12 | +1 % |

**Sell-pressure overrides** (from on-site net buys):
- `net_buy < -5` → override to **−2.5 %**
- `net_buy < -10` → override to **−4 %**

**Mean-reversion cap:** if `beri > base_beri × 3`, upward proposals are capped at
**+0.5 %** — prevents already-inflated characters from running away.

## LLM passes

The engine is rule-based tiers; LLMs add *direction and second-order reasoning*,
not the base scoring. Models are routed in `app/vegapunk_llm.py`:

- **haiku** (`claude-haiku-4-5`) — fast/cheap bulk generation passes.
- **sonnet** (`claude-sonnet-4-6`) — precise resolution calls.
- **opus** (`claude-opus-4-8`) — reasoning-heavy work, incl. the Monday
  implications pass.

Key LLM-driven behaviors:
- **Sentiment/direction** (`_sentiment_verdict`) — scales moves by magnitude so
  defeated/diminished characters *drop* rather than rise on mere mention volume.
- **Implications pass** (`run_implications_pass`) — an opus pass over the wiki
  summary surfaces weekend speculation, historic connections, and characters
  affected beyond the chapter's direct content → the smaller Wave 2 proposals.

All LLM passes need `ANTHROPIC_API_KEY`. Without it they degrade gracefully to
signal-only scoring (no crash, just no direction/implication layer).
