"""
Database connection and session management.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from .settings import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool if settings.testing else None,
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for models
Base = declarative_base()


async def get_db() -> AsyncSession:
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database tables and add missing columns."""
    from sqlalchemy import text
    import logging

    logger = logging.getLogger(__name__)

    async with engine.begin() as conn:
        # Create tables that don't exist
        await conn.run_sync(Base.metadata.create_all)

        # Add missing columns to existing tables (SQLAlchemy create_all doesn't do this)
        try:
            await conn.execute(text(
                "ALTER TABLE call_assignments ADD COLUMN IF NOT EXISTS attending_name VARCHAR(100)"
            ))
            logger.info("Ensured attending_name column exists")
        except Exception as e:
            # Might fail if table doesn't exist yet or syntax not supported
            logger.debug(f"Column migration note: {e}")


async def close_db():
    """Close database connections."""
    await engine.dispose()
