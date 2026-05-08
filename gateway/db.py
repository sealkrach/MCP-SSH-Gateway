"""
Shared DB infrastructure — engine, Base, Session, get_db
Imported by both main.py and auth.py to avoid circular imports.
"""
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./gateway.db")

# Pool tuning applies to PostgreSQL (asyncpg) only — aiosqlite ignores these kwargs
_is_sqlite = DATABASE_URL.startswith("sqlite")
_pool_kwargs = {} if _is_sqlite else {
    "pool_size": 20,
    "max_overflow": 10,
    "pool_recycle": 3600,
    "pool_pre_ping": True,
}

engine  = create_async_engine(DATABASE_URL, echo=False, **_pool_kwargs)
Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase): pass

async def get_db():
    async with Session() as s:
        yield s
