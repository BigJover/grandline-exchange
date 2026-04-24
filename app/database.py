from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./grandline.db")

# SQLite needs this flag; ignored by Postgres
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

pool_kwargs = {}
if not DATABASE_URL.startswith("sqlite"):
    pool_kwargs = {
        "pool_size": 5,
        "max_overflow": 5,
        "pool_pre_ping": True,
        "pool_recycle": 3600,   # recycle connections every 60 min
        "pool_timeout": 15,
    }

engine = create_engine(DATABASE_URL, connect_args=connect_args, **pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
