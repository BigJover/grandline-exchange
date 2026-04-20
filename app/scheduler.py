from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

WEEKLY_DROP = 10_000  # beri per real user per drop


def run_beri_drop():
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        users = db.query(models.User).filter(models.User.is_bot == False).all()
        for user in users:
            user.beri_balance += WEEKLY_DROP
        db.commit()
        print(f"[Beri Drop] +{WEEKLY_DROP:,}฿ distributed to {len(users)} users")
    except Exception as e:
        db.rollback()
        print(f"[Beri Drop] Error: {e}")
    finally:
        db.close()


scheduler = AsyncIOScheduler(timezone="UTC")
scheduler.add_job(
    run_beri_drop,
    CronTrigger(day_of_week="sun", hour=0, minute=0, second=0),
    id="weekly_beri_drop",
    replace_existing=True,
)
