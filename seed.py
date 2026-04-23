"""
Seed the database with characters from data/characters.json.
Run once: python seed.py
"""
import json
from app.database import SessionLocal, engine, Base
from app import models

Base.metadata.create_all(bind=engine)

DATA_PATH = "data/characters.json"

def seed():
    with open(DATA_PATH) as f:
        chars = json.load(f)

    db = SessionLocal()
    try:
        existing = db.query(models.Character).count()
        if existing:
            print(f"DB already has {existing} characters — skipping seed.")
            return

        for c in chars:
            character = models.Character(
                name=c.get("name", ""),
                aliases=c.get("aliases", []),
                faction=c.get("faction", ""),
                category=c.get("category", ""),
                beri=c.get("beri", 0),
                canon_bounty=c.get("canon_bounty"),
                status=c.get("status", "active"),
                notes=c.get("notes", ""),
                rank=c.get("rank", 0),
                price_history=c.get("price_history", []),
                img=c.get("img", ""),
                bio=c.get("bio", ""),
                events=c.get("events", ""),
                sbs=c.get("sbs", []),
                related=c.get("related", []),
            )
            db.add(character)

        db.commit()
        print(f"Seeded {len(chars)} characters.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
