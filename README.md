# Grand Line Stock Exchange 🏴‍☠️📈

**A live, fully-automated fan stock market for One Piece.** Every character is a stock.
Prices move when the story does — a weekly AI pipeline reads each new chapter and
re-prices the market, no manual admin required. Trade Luffy, short a Yonko, and put
your Berries where your theories are.

> Fan project. All One Piece characters and materials belong to Eiichiro Oda,
> Shueisha, and Toei Animation. No real money anywhere — the only currency is Berries.

## What's in the market

- **340+ tradeable characters** with live prices over WebSockets, portfolios,
  transaction history, and leaderboards
- **Chapter predictions** — weekly slates of story predictions with Berry payouts,
  generated and resolved alongside the chapter cycle
- **Davy Back Challenges** — casino-style Berry games ⚔
- **Trivia**, character pages with canon-grounded bios, comments, and community requests
- **Vegapunk** — the resident Discord bot: market chatter, announcements, and
  chapter-day intel piped to the community server

## The interesting part: the market runs itself

A three-phase weekly cycle, scheduled around the manga's release rhythm:

1. **Chapter drop (Sat):** the pipeline scrapes the chapter summary from the wiki,
   cross-references YouTube reaction volume, and has Claude read the summary to score
   each involved character's *direction and magnitude* — a character getting defeated
   drops even if everyone is talking about them. Break weeks are detected and skipped.
2. **Implications pass (Mon):** a second, slower Claude pass over the same chapter for
   knock-on effects — reveals and setups that re-price characters who weren't on-panel.
3. **Buzz (Mon–Thu):** community discussion volume nudges prices between chapters,
   plus ambient drift so the market never sits perfectly still.

Bot market makers provide baseline liquidity, and price changes flow through a queue so
spikes land as movement, not chaos. Proposed changes upsert idempotently — re-running a
pass never double-applies.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI + SQLAlchemy + Pydantic |
| Data | PostgreSQL (SQLite for local dev) |
| Auth | JWT (python-jose) + bcrypt, rate-limited via slowapi |
| Realtime | native WebSockets (live ticker) |
| Scheduling | APScheduler — the whole weekly cycle is cron-in-app |
| AI | Claude (Anthropic API) for chapter reading, implications, and Vegapunk's voice |
| Discord | discord.py bot + webhook announcements |
| Frontend | hand-rolled static HTML/CSS/JS — "Punk Records" theme, zero framework |
| Deploy | Railway (Procfile) |

## Running locally

```bash
pip install -r requirements.txt
python seed.py        # seed characters into a local SQLite db
uvicorn app.main:app --reload
```

Then open `http://localhost:8000`. Everything secret is environment-driven
(`DATABASE_URL`, `SECRET_KEY`, `ADMIN_SECRET`, `ANTHROPIC_API_KEY`,
`DISCORD_*`, `YOUTUBE_API_KEY`) — with none set you get a working local market with
seeded data; the AI/Discord layers just stay dormant. Admin routes live under `/admin`
and fail closed without a configured secret.

## Content policy

Character bios and market events stick to canon, SBS, and only the most widely-held
community theories — the market reacts to the story, it doesn't invent one.
