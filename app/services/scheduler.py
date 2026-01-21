"""
Background job scheduler for automated tasks.

Uses APScheduler to run periodic jobs like Amion syncing.
"""
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session_maker
from ..settings import settings
from .amion_scraper import run_amion_sync

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manages background scheduled jobs."""

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._running = False

    def start(self):
        """Start the scheduler with configured jobs."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self.scheduler = AsyncIOScheduler()

        # Add Amion sync job if configured
        if settings.amion_base_url:
            self.scheduler.add_job(
                self._run_amion_sync_job,
                CronTrigger(hour=settings.amion_sync_hour, minute=0),
                id="amion_daily_sync",
                name="Daily Amion Sync",
                replace_existing=True,
            )
            logger.info(
                f"Scheduled Amion sync job to run daily at {settings.amion_sync_hour:02d}:00"
            )
        else:
            logger.info("Amion sync not scheduled - AMION_BASE_URL not configured")

        self.scheduler.start()
        self._running = True
        logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler and self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    def get_jobs(self) -> list:
        """Get list of scheduled jobs."""
        if not self.scheduler:
            return []

        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in self.scheduler.get_jobs()
        ]

    async def _run_amion_sync_job(self):
        """Execute the Amion sync job."""
        logger.info("Starting scheduled Amion sync...")
        start_time = datetime.utcnow()

        async with async_session_maker() as db:
            try:
                results = await run_amion_sync(
                    db=db,
                    months_to_sync=2,  # Sync current and next month
                )
                await db.commit()

                duration = (datetime.utcnow() - start_time).total_seconds()
                logger.info(
                    f"Scheduled Amion sync completed in {duration:.1f}s. "
                    f"Processed {results.get('call_entries_processed', 0)} call entries."
                )

            except Exception as e:
                await db.rollback()
                logger.error(f"Scheduled Amion sync failed: {e}", exc_info=True)

    async def trigger_amion_sync_now(self) -> dict:
        """Manually trigger an Amion sync (outside of schedule)."""
        logger.info("Manually triggered Amion sync...")

        async with async_session_maker() as db:
            try:
                results = await run_amion_sync(db=db, months_to_sync=1)
                await db.commit()
                return {"status": "success", "results": results}
            except Exception as e:
                await db.rollback()
                logger.error(f"Manual Amion sync failed: {e}", exc_info=True)
                return {"status": "error", "error": str(e)}


# Global scheduler instance
scheduler = SchedulerService()
