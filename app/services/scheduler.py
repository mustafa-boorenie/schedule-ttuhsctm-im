"""
Background job scheduler for automated tasks.

Uses APScheduler to run periodic jobs like Amion syncing.
"""
import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session_maker
from ..settings import settings
from .amion_scraper import run_amion_sync, sync_hospitalist_call_schedule

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

        # Hospitalist team-call sync windows:
        # - nightly for current month
        # - weekly for next 3 months
        self._schedule_hospitalist_call_jobs()

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

    def _schedule_hospitalist_call_jobs(self):
        """Schedule hospitalist call-sync jobs if required URLs are configured."""
        if not self.scheduler:
            return

        if not settings.amion_all_rows_url or not settings.amion_oncall_url:
            logger.info(
                "Hospitalist call sync not scheduled - AMION_ALL_ROWS_URL/AMION_ONCALL_URL not configured"
            )
            return

        today = date.today()
        current_month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])

        # Next month start and end of 3-month horizon after current month.
        next_month_start = current_month_end + timedelta(days=1)
        horizon_year, horizon_month = _add_months(today.year, today.month, 3)
        horizon_end = date(horizon_year, horizon_month, monthrange(horizon_year, horizon_month)[1])

        self.scheduler.add_job(
            self._run_hospitalist_call_sync_job,
            CronTrigger(
                hour=settings.amion_sync_hour,
                minute=0,
                start_date=datetime.combine(today, time(0, 0)),
                end_date=datetime.combine(current_month_end, time(23, 59)),
            ),
            kwargs={"scope": "current_month_nightly"},
            id="amion_hospitalist_current_month_nightly",
            name="Hospitalist Call Sync (Nightly Current Month)",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._run_hospitalist_call_sync_job,
            CronTrigger(
                day_of_week="sun",
                hour=settings.amion_sync_hour,
                minute=15,
                start_date=datetime.combine(next_month_start, time(0, 0)),
                end_date=datetime.combine(horizon_end, time(23, 59)),
            ),
            kwargs={"scope": "next_three_months_weekly"},
            id="amion_hospitalist_next_three_months_weekly",
            name="Hospitalist Call Sync (Weekly Next 3 Months)",
            replace_existing=True,
        )

        logger.info(
            "Scheduled hospitalist call sync jobs: nightly current month through %s and weekly through %s",
            current_month_end.isoformat(),
            horizon_end.isoformat(),
        )

    async def _run_hospitalist_call_sync_job(self, scope: str):
        """Execute scheduled hospitalist call sync for the current calendar month."""
        target = date.today()
        logger.info(
            "Starting scheduled hospitalist call sync (%s) for %04d-%02d",
            scope,
            target.year,
            target.month,
        )

        async with async_session_maker() as db:
            try:
                results = await sync_hospitalist_call_schedule(
                    db=db,
                    all_rows_url=settings.amion_all_rows_url,
                    oncall_url=settings.amion_oncall_url,
                    year=target.year,
                    month=target.month,
                )
                await db.commit()
                logger.info(
                    "Scheduled hospitalist call sync completed (%s): generated=%s created=%s updated=%s",
                    scope,
                    results.get("call_assignments_generated", 0),
                    results.get("created", 0),
                    results.get("updated", 0),
                )
            except Exception as e:
                await db.rollback()
                logger.error("Scheduled hospitalist call sync failed (%s): %s", scope, e, exc_info=True)

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


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    """Add offset months to (year, month), returning normalized tuple."""
    month_index = (year * 12 + (month - 1)) + offset
    out_year = month_index // 12
    out_month = (month_index % 12) + 1
    return out_year, out_month
