import os
import fcntl
import time
import random
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_BERI_LOCK       = "/tmp/beri_drop.lock"
_PURGE_LOCK      = "/tmp/comment_purge.lock"
_BOT_LOCK        = "/tmp/bot_tick.lock"
_CHAPTER_LOCK    = "/tmp/chapter_detect.lock"
_PREDICTION_LOCK = "/tmp/prediction_gen.lock"
_YOUTUBE_LOCK    = "/tmp/youtube_enrich.lock"
_VOLATILITY_LOCK = "/tmp/volatility_digest.lock"
_MIN_INTERVAL    = 23 * 3600  # must be at least 23h since last run


def _should_run(lock_path: str, min_seconds: int = _MIN_INTERVAL) -> bool:
    """Exclusive file lock guard — only the first worker to acquire it within
    min_seconds will return True. All others skip immediately."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)          # block until we hold the lock
            content = os.read(fd, 32).decode().strip()
            last_ts = float(content) if content else 0.0
            now_ts = time.time()
            if now_ts - last_ts < min_seconds:
                return False                          # another worker already ran this cycle
            # Claim the slot before releasing so the next worker sees it
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(now_ts).encode())
            return True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception as e:
        print(f"[Scheduler] Lock check failed ({lock_path}): {e} — proceeding")
        return True

WEEKLY_DROP = 10_000        # fallback for users with no faction
ROYALTY_LOGIN_PASSIVE = 75_000
CITIZEN_BASE = 30_000
CREATURE_TIDAL = 150_000
REV_BASE = 50_000
REV_PER_WG_USER = 5_000
ROYALTY_TAX_RATE = 0.15     # Marines pay 15% to Royalty
CITIZEN_TAX_RATE = 0.10     # Marines pay 10% to Citizens


def _marine_base_salary(beri_balance: float) -> int:
    if beri_balance < 1_000_000:
        return 50_000
    elif beri_balance < 10_000_000:
        return 100_000
    elif beri_balance < 100_000_000:
        return 200_000
    elif beri_balance < 500_000_000:
        return 350_000
    else:
        return 500_000


def _bounty_heat(beri_balance: float):
    """Returns (hit: bool, loss_fraction: float).
    Higher balance = bigger target = higher chance of getting hunted."""
    if beri_balance < 1_000_000:
        chance, lo, hi = 0.05, 0.10, 0.30
    elif beri_balance < 50_000_000:
        chance, lo, hi = 0.10, 0.15, 0.35
    elif beri_balance < 500_000_000:
        chance, lo, hi = 0.20, 0.20, 0.40
    else:
        chance, lo, hi = 0.30, 0.25, 0.50
    if random.random() < chance:
        return True, random.uniform(lo, hi)
    return False, 0.0


def _pirate_plunder() -> int:
    roll = random.random()
    if roll < 0.60:
        return random.randint(5_000, 100_000)          # 60% — small haul
    elif roll < 0.85:
        return random.randint(100_000, 1_000_000)      # 25% — decent plunder
    elif roll < 0.97:
        return random.randint(1_000_000, 5_000_000)    # 12% — major raid
    else:
        return random.randint(5_000_000, 10_000_000)   #  3% — legendary haul


def run_beri_drop():
    if not _should_run(_BERI_LOCK):
        print("[Beri Drop] Already ran this cycle (another worker) — skipping")
        return

    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        users = db.query(models.User).filter(models.User.is_bot == False).all()

        # Bucket users by faction
        buckets: dict[str, list] = {}
        for u in users:
            key = u.user_faction or "none"
            buckets.setdefault(key, []).append(u)

        marines       = buckets.get("marine", [])
        royalty_users = buckets.get("royalty", [])
        citizens      = buckets.get("citizen", [])
        revolutionaries = buckets.get("revolutionary", [])
        pirates       = buckets.get("pirate", [])
        creatures     = buckets.get("creature", [])
        unfactioned   = buckets.get("none", [])

        events = []

        # ── MARINE: salary with tax deductions ──────────────────────────────
        royalty_pool = 0
        citizen_pool = 0
        for u in marines:
            base = _marine_base_salary(u.beri_balance)
            to_royalty = int(base * ROYALTY_TAX_RATE)
            to_citizens = int(base * CITIZEN_TAX_RATE)
            net = base - to_royalty - to_citizens
            u.beri_balance += net
            royalty_pool += to_royalty
            citizen_pool += to_citizens
            events.append(models.BeriEvent(
                user_id=u.id,
                event_type="salary",
                amount=net,
                description=f"Marine salary \u2014 {net:,}\u0e3f kept, {to_royalty + to_citizens:,}\u0e3f tribute paid",
            ))

        # ── ROYALTY: passive + Marine tribute (proportional by balance) ─────
        if royalty_users:
            total_royalty_bal = sum(u.beri_balance for u in royalty_users) or 1
            for u in royalty_users:
                tribute_share = int(royalty_pool * (u.beri_balance / total_royalty_bal)) if royalty_pool else 0
                total = ROYALTY_LOGIN_PASSIVE + tribute_share
                u.beri_balance += total
                events.append(models.BeriEvent(
                    user_id=u.id,
                    event_type="royalty_income",
                    amount=total,
                    description=f"Noble income \u2014 {ROYALTY_LOGIN_PASSIVE:,}\u0e3f passive + {tribute_share:,}\u0e3f Marine tribute",
                ))

        # ── CITIZEN: base bonus + equal split of citizen tax pool ───────────
        if citizens:
            per_citizen = int(citizen_pool / len(citizens)) if citizen_pool else 0
            for u in citizens:
                total = CITIZEN_BASE + per_citizen
                u.beri_balance += total
                events.append(models.BeriEvent(
                    user_id=u.id,
                    event_type="citizen_income",
                    amount=total,
                    description=f"Weekly dividend \u2014 {CITIZEN_BASE:,}\u0e3f base + {per_citizen:,}\u0e3f protection fund",
                ))

        # ── REVOLUTIONARY: scales with WG activity ──────────────────────────
        if revolutionaries:
            wg_count = len(marines) + len(royalty_users)
            rev_income = REV_BASE + wg_count * REV_PER_WG_USER
            for u in revolutionaries:
                u.beri_balance += rev_income
                events.append(models.BeriEvent(
                    user_id=u.id,
                    event_type="revolutionary_income",
                    amount=rev_income,
                    description=f"Revolutionary fund \u2014 {REV_BASE:,}\u0e3f base + {wg_count * REV_PER_WG_USER:,}\u0e3f from {wg_count} WG targets",
                ))

        # ── PIRATE: plunder then bounty hunter heat check ────────────────────
        now = datetime.now(timezone.utc)
        for u in pirates:
            loot = _pirate_plunder()
            u.beri_balance += loot
            events.append(models.BeriEvent(
                user_id=u.id,
                event_type="plunder",
                amount=loot,
                description=f"Plunder \u2014 {loot:,}\u0e3f seized",
            ))

            # Warlords are immune to heat checks
            is_warlord = u.warlord_until and u.warlord_until > now
            if not is_warlord:
                hit, loss_frac = _bounty_heat(u.beri_balance)
                if hit:
                    loss = int(u.beri_balance * loss_frac)
                    u.beri_balance = max(0, u.beri_balance - loss)
                    events.append(models.BeriEvent(
                        user_id=u.id,
                        event_type="bounty_hunter",
                        amount=-loss,
                        description=f"Bounty hunter raid \u2014 {loss:,}\u0e3f seized ({loss_frac*100:.0f}% of wallet)",
                    ))

        # ── CREATURE: flat tidal income ──────────────────────────────────────
        for u in creatures:
            u.beri_balance += CREATURE_TIDAL
            events.append(models.BeriEvent(
                user_id=u.id,
                event_type="tidal_income",
                amount=CREATURE_TIDAL,
                description=f"Tidal income \u2014 {CREATURE_TIDAL:,}\u0e3f",
            ))

        # ── UNFACTIONED: flat base drop ──────────────────────────────────────
        for u in unfactioned:
            u.beri_balance += WEEKLY_DROP
            events.append(models.BeriEvent(
                user_id=u.id,
                event_type="base_drop",
                amount=WEEKLY_DROP,
                description=f"Weekly beri drop \u2014 {WEEKLY_DROP:,}\u0e3f (choose a faction to unlock better income)",
            ))

        db.bulk_save_objects(events)
        db.commit()
        print(f"[Beri Drop] {len(users)} users | marines:{len(marines)} royalty:{len(royalty_users)} "
              f"citizens:{len(citizens)} revs:{len(revolutionaries)} "
              f"pirates:{len(pirates)} creatures:{len(creatures)} none:{len(unfactioned)}")

        return {
            "users_paid": len(users),
            "net_distributed": sum(e.amount for e in events),
            "by_faction": {
                "marine": len(marines),
                "royalty": len(royalty_users),
                "citizen": len(citizens),
                "revolutionary": len(revolutionaries),
                "pirate": len(pirates),
                "creature": len(creatures),
                "unfactioned": len(unfactioned),
            },
        }

    except Exception as e:
        db.rollback()
        print(f"[Beri Drop] Error: {e}")
        return None
    finally:
        db.close()


def run_comment_purge():
    """Delete comments from previous weeks with fewer than 2 likes."""
    if not _should_run(_PURGE_LOCK):
        print("[Comment Purge] Already ran this cycle (another worker) — skipping")
        return

    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        iso = now.isocalendar()
        current = f"{iso[0]}-W{iso[1]:02d}"
        deleted = (
            db.query(models.Comment)
            .filter(models.Comment.week != current, models.Comment.likes < 2)
            .delete(synchronize_session=False)
        )
        db.commit()
        print(f"[Comment Purge] Deleted {deleted} low-engagement comments")
    except Exception as e:
        db.rollback()
        print(f"[Comment Purge] Error: {e}")
    finally:
        db.close()


def _run_bot_tick_guarded():
    if not _should_run(_BOT_LOCK):
        print("[BotTick] Already ran today (another worker) — skipping")
        return
    from app.bots import run_bot_tick
    run_bot_tick()


def _run_prediction_generate_guarded():
    """Generate predictions for the most recently detected chapter.
    Fires Saturday 14:00 UTC — ~24h after typical Friday chapter drop.
    Idempotent: skips if already ran for the same chapter.

    Pre-pass before generating: the initial scrape at detection time often
    runs before discussion (and break-week announcements) have accumulated,
    so re-scrape signals and re-check the break flag with matured data."""
    if not _should_run(_PREDICTION_LOCK, min_seconds=20 * 3600):
        return
    try:
        from app.database import SessionLocal
        from app import models as _m
        from sqlalchemy import func as sqlfunc
        from app.prediction_pipeline import generate_chapter_predictions
        db = SessionLocal()
        try:
            latest = db.query(sqlfunc.max(_m.Chapter.number)).scalar()
            if not latest:
                print("[PredictionScheduler] No chapters in DB yet — skipping")
                return

            try:
                from app.chapter_pipeline import detect_chapter_drop, _detect_break_week
                from app.prediction_pipeline import _already_ran
                if not _already_ran(db, latest):
                    # Re-scrape only if the admin hasn't acted on any proposals
                    # yet — a forced re-run wipes pending rows, and recreating
                    # ones that were already approved would risk double-applying
                    # price changes.
                    acted = db.query(_m.ProposedPriceChange).filter(
                        _m.ProposedPriceChange.chapter_number == latest,
                        _m.ProposedPriceChange.status != "pending",
                    ).count()
                    if acted == 0:
                        rescrape = detect_chapter_drop(db, force_chapter=latest, announce=False)
                        print(f"[PredictionScheduler] Ch.{latest} re-scrape: {rescrape['proposals']} proposals")
                    else:
                        # Proposals already screened — just re-check break week,
                        # since the one-shot detection at processing time can run
                        # before break announcements land on Reddit.
                        chapter_row = db.query(_m.Chapter).filter(_m.Chapter.number == latest).first()
                        if chapter_row and not chapter_row.next_is_break and _detect_break_week(latest):
                            chapter_row.next_is_break = True
                            db.commit()
                            print(f"[PredictionScheduler] Ch.{latest}: break week detected on re-check")
            except Exception as e:
                print(f"[PredictionScheduler] Pre-pass failed (non-fatal): {e}")

            result = generate_chapter_predictions(db, latest)
            if result["skipped"]:
                print(f"[PredictionScheduler] Ch.{latest} predictions already generated")
            else:
                print(f"[PredictionScheduler] Ch.{latest}: {result['created']} live, {result['drafts']} drafts")

            # Wave 2 — publish the matured synopsis for the Vegapunk bot to
            # announce. Runs regardless of the re-scrape gate above: the gate
            # only protects proposal re-creation, not the synopsis readout.
            try:
                from app.chapter_pipeline import publish_chapter_synopsis
                publish_chapter_synopsis(db, latest)
            except Exception as e:
                print(f"[PredictionScheduler] Synopsis publish failed (non-fatal): {e}")

            # Resolution retry — first detection (Thu 03:00) can hit a TBA wiki
            # page, which defers auto-resolve (or historically left props stuck
            # in needs_review). The wiki has matured by Saturday: re-fetch the
            # character list and give every unresolved prop targeting this
            # chapter another shot. auto_resolve defers again if still empty.
            try:
                from app.chapter_pipeline import _wiki_chapter_chars, _char_index_from_db
                from app.prediction_pipeline import auto_resolve_predictions
                wiki_chars = _wiki_chapter_chars(latest, _char_index_from_db(db))
                rr = auto_resolve_predictions(db, latest, wiki_chars)
                if rr["resolved"] or rr["deferred"]:
                    print(f"[PredictionScheduler] Ch.{latest} resolution retry: "
                          f"{rr['resolved']} resolved, deferred={rr['deferred']}")
            except Exception as e:
                print(f"[PredictionScheduler] Resolution retry failed (non-fatal): {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"[PredictionScheduler] Error: {e}")


def _run_youtube_enrich_guarded():
    """Enrich latest chapter proposals with YouTube reaction video sentiment.
    Fires Sunday 14:00 UTC — ~48h after Friday chapter drop, when reaction
    videos have meaningful view counts and comment activity.
    Idempotent: proposals already tagged with YouTube signal are skipped."""
    if not _should_run(_YOUTUBE_LOCK, min_seconds=20 * 3600):
        return
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("[YouTubeEnrich] YOUTUBE_API_KEY not set — skipping")
        return
    try:
        from app.database import SessionLocal
        from app import models as _m
        from sqlalchemy import func as sqlfunc
        from app.chapter_pipeline import enrich_chapter_with_youtube
        db = SessionLocal()
        try:
            latest = db.query(sqlfunc.max(_m.Chapter.number)).scalar()
            if not latest:
                print("[YouTubeEnrich] No chapters in DB yet — skipping")
                return
            result = enrich_chapter_with_youtube(db, latest)
            print(
                f"[YouTubeEnrich] Ch.{latest}: {result['yt_chars_found']} chars found, "
                f"{result['proposals_updated']} updated, {result['proposals_added']} added"
            )
        finally:
            db.close()
    except Exception as e:
        print(f"[YouTubeEnrich] Error: {e}")


_VOLATILITY_SNAPSHOT_KEY = "volatility_snapshot"


def compute_volatility_digest(db, post: bool = True, demo: bool = False) -> dict:
    """Rank characters by absolute price move since the last weekly snapshot and
    (optionally) post the top 5 to the price-alert Discord channel.

    Week-over-week: compares current beri to a snapshot stored in BotKV, then
    refreshes that snapshot for next week. The first ever run only seeds the
    snapshot (nothing to compare against) and posts nothing.

    demo=True compares against each character's previous price_history entry
    instead of the snapshot — lets an admin fire a populated test post on demand
    without disturbing the weekly snapshot.
    """
    import json as _json
    from app import models
    from app.discord_notify import announce_weekly_volatility

    chars = db.query(models.Character).all()
    cur   = {str(c.id): c.beri for c in chars}
    names = {str(c.id): c.name for c in chars}

    if demo:
        prev = {}
        for c in chars:
            hist = c.price_history or []
            if hist and isinstance(hist[-1], dict) and hist[-1].get("beri"):
                prev[str(c.id)] = float(hist[-1]["beri"])
        seeded = False
    else:
        row = db.query(models.BotKV).filter(
            models.BotKV.key == _VOLATILITY_SNAPSHOT_KEY
        ).first()
        prev = {}
        if row and row.value:
            try:
                prev = _json.loads(row.value)
            except Exception:
                prev = {}
        seeded = not prev
        # Refresh the snapshot for next week (only the real, non-demo path)
        new_val = _json.dumps(cur)
        if row:
            row.value = new_val
        else:
            db.add(models.BotKV(key=_VOLATILITY_SNAPSHOT_KEY, value=new_val))
        db.commit()

    moves = []
    for cid, beri in cur.items():
        old = prev.get(cid)
        if old and old > 0:
            pct = (beri - old) / old * 100.0
            if abs(pct) >= 0.1:           # ignore essentially-flat coefficients
                moves.append({"name": names[cid], "pct": pct, "new_beri": beri})
    moves.sort(key=lambda m: abs(m["pct"]), reverse=True)
    top5 = moves[:5]

    iso = datetime.now(timezone.utc).isocalendar()
    week_label = f"{iso[0]}-W{iso[1]:02d}"

    posted = False
    if post and top5 and (not seeded or demo):
        try:
            posted = announce_weekly_volatility(top5, week_label)
        except Exception as e:
            print(f"[VolatilityDigest] Discord post failed (non-fatal): {e}")

    return {
        "seeded": seeded and not demo,
        "demo": demo,
        "posted": posted,
        "week": week_label,
        "movers": [
            {"name": m["name"], "pct": round(m["pct"], 2), "new_beri": m["new_beri"]}
            for m in top5
        ],
    }


def run_volatility_digest():
    """Weekly job — post the five most volatile characters of the week.
    Lock-guarded so only one worker fires it; ~weekly cooldown."""
    if not _should_run(_VOLATILITY_LOCK, min_seconds=5 * 24 * 3600):
        return
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        result = compute_volatility_digest(db, post=True)
        if result["seeded"]:
            print("[VolatilityDigest] Snapshot seeded (first run) — no post")
        else:
            print(f"[VolatilityDigest] {result['week']}: posted={result['posted']} "
                  f"top={[m['name'] for m in result['movers']]}")
    except Exception as e:
        db.rollback()
        print(f"[VolatilityDigest] Error: {e}")
    finally:
        db.close()


def _run_chapter_detect_guarded():
    """Run chapter drop detection. Called Thu 03:00 UTC (chapter drop + leak window)
    and Sun 03:00 UTC (episode release / second-wave discussion).
    The pipeline itself is idempotent — skips chapters already processed."""
    if not _should_run(_CHAPTER_LOCK, min_seconds=3 * 3600):   # 3h cooldown guards against double-fire on multi-worker
        return
    try:
        from app.database import SessionLocal
        from app.chapter_pipeline import detect_chapter_drop
        db = SessionLocal()
        try:
            result = detect_chapter_drop(db)
            if result["detected"]:
                print(f"[ChapterDetect] {result['message']}")
            else:
                print(f"[ChapterDetect] {result['message']}")
        finally:
            db.close()
    except Exception as e:
        print(f"[ChapterDetect] Error: {e}")


scheduler = AsyncIOScheduler(timezone="UTC")
# Weekly beri drop — fixed schedule for consistent user income, decoupled
# from chapter detection (which can misfire on breaks/early wiki stubs).
scheduler.add_job(
    run_beri_drop,
    CronTrigger(day_of_week="sun", hour=0, minute=0, second=0),
    id="weekly_beri_drop",
    replace_existing=True,
)
scheduler.add_job(
    _run_bot_tick_guarded,
    CronTrigger(hour=12, minute=0, second=0),   # once daily at noon UTC
    id="bot_market_tick",
    replace_existing=True,
)
scheduler.add_job(
    _run_chapter_detect_guarded,
    CronTrigger(day_of_week="thu", hour=3, minute=0, second=0),  # chapter drop + leak window
    id="chapter_detect_thu",
    replace_existing=True,
)
scheduler.add_job(
    _run_chapter_detect_guarded,
    CronTrigger(day_of_week="sun", hour=3, minute=0, second=0),  # episode release / second-wave discussion
    id="chapter_detect_sun",
    replace_existing=True,
)
scheduler.add_job(
    _run_prediction_generate_guarded,
    CronTrigger(day_of_week="sat", hour=14, minute=0, second=0),  # ~24h after Friday chapter drop
    id="prediction_generate_sat",
    replace_existing=True,
)
scheduler.add_job(
    _run_youtube_enrich_guarded,
    CronTrigger(day_of_week="sun", hour=14, minute=0, second=0),  # ~48h after Friday chapter drop
    id="youtube_enrich_sun",
    replace_existing=True,
)
scheduler.add_job(
    run_volatility_digest,
    CronTrigger(day_of_week="sun", hour=20, minute=0, second=0),  # end-of-week market wrap
    id="weekly_volatility_digest",
    replace_existing=True,
)
# Comment purge disabled until there's a surplus of comments
# scheduler.add_job(
#     run_comment_purge,
#     CronTrigger(day_of_week="sun", hour=0, minute=5, second=0),
#     id="weekly_comment_purge",
#     replace_existing=True,
# )
