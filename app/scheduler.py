import os
import fcntl
import time
import random
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_BERI_LOCK = "/tmp/beri_drop.lock"
_PURGE_LOCK = "/tmp/comment_purge.lock"
_MIN_INTERVAL = 23 * 3600  # must be at least 23h since last run


def _should_run(lock_path: str) -> bool:
    """Exclusive file lock guard — only the first worker to acquire it within
    a 23-hour window will return True. All others skip immediately."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)          # block until we hold the lock
            content = os.read(fd, 32).decode().strip()
            last_ts = float(content) if content else 0.0
            now_ts = time.time()
            if now_ts - last_ts < _MIN_INTERVAL:
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

    except Exception as e:
        db.rollback()
        print(f"[Beri Drop] Error: {e}")
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


scheduler = AsyncIOScheduler(timezone="UTC")
scheduler.add_job(
    run_beri_drop,
    CronTrigger(day_of_week="sun", hour=0, minute=0, second=0),
    id="weekly_beri_drop",
    replace_existing=True,
)
scheduler.add_job(
    run_comment_purge,
    CronTrigger(day_of_week="sun", hour=0, minute=5, second=0),
    id="weekly_comment_purge",
    replace_existing=True,
)
