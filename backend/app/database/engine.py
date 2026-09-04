"""
Synchronous SQLAlchemy engine — used exclusively by Alembic for migrations.
Runtime queries use asyncpg (see connection.py).
"""
from sqlalchemy import create_engine
from app.config import settings

# asyncpg uses postgresql+asyncpg:// but sync engine needs postgresql://
# Replace scheme if needed
_sync_url = settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql://"
)

sync_engine = create_engine(_sync_url, echo=False)
