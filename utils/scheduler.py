import logging
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.base import JobLookupError # Import for handling

logger = logging.getLogger(__name__)

# --- Scheduler Setup ---
jobstores = {'default': MemoryJobStore()}
executors = {'default': AsyncIOExecutor()}
job_defaults = {'coalesce': False, 'max_instances': 3} # Allow multiple digest jobs? Maybe set max_instances=1 for digest?
scheduler = AsyncIOScheduler(
    jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone='UTC'
)

def start_scheduler():
    if not scheduler.running:
        try:
            scheduler.start()
            logger.info("APScheduler started.")
        except Exception as e:
            logger.exception(f"Failed to start APScheduler: {e}")
    else: logger.warning("APScheduler already running.")

def stop_scheduler():
    if scheduler.running:
        try:
            scheduler.shutdown()
            logger.info("APScheduler shut down.")
        except Exception as e:
            logger.error(f"Error shutting down APScheduler: {e}")


async def schedule_task(task_func, job_id: str, trigger_type: str = 'date', replace_existing: bool = True, args: list = None, kwargs: dict = None, **trigger_kwargs):
    """
    Schedules a task using various triggers (date, cron, interval).

    Args:
        task_func: The async function to execute.
        job_id: Unique ID for the job.
        trigger_type: 'date', 'cron', or 'interval'.
        replace_existing: Whether to replace an existing job with the same ID.
        args: List of positional arguments for task_func.
        kwargs: Dictionary of keyword arguments for task_func.
        **trigger_kwargs: Arguments specific to the trigger (e.g., run_date, hour, minute, seconds, timezone).
                           For 'cron', common args are hour, minute, day_of_week, etc.
                           For 'interval', common args are seconds, minutes, hours.
                           'timezone' can be passed for 'cron'.
    """
    if trigger_type == 'date':
        run_date = trigger_kwargs.get('run_date')
        if not isinstance(run_date, datetime):
            raise TypeError("run_date (datetime object) is required for date trigger")
        # Ensure timezone awareness for comparison
        now_aware = datetime.now(run_date.tzinfo if run_date.tzinfo else timezone.utc)
        if run_date < now_aware:
             logger.warning(f"Attempted to schedule date job {job_id} in the past ({run_date}). Skipping.")
             return None

    # Basic check for required cron args
    if trigger_type == 'cron' and not ('hour' in trigger_kwargs or 'minute' in trigger_kwargs or 'second' in trigger_kwargs):
         logger.warning(f"Scheduling cron job {job_id} without hour/minute/second. Will run very frequently if no other constraints set.")


    try:
        job = scheduler.add_job(
            task_func,
            trigger=trigger_type,
            args=args or [],
            kwargs=kwargs or {},
            id=job_id,
            replace_existing=replace_existing,
            misfire_grace_time=trigger_kwargs.pop('misfire_grace_time', 3600), # Allow custom or default grace time
            **trigger_kwargs # Pass remaining kwargs to the specific trigger
        )
        logger.info(f"Scheduled job '{job.id}' with trigger '{trigger_type}' args: {trigger_kwargs}")
        return job # Return job object
    except Exception as e:
        logger.exception(f"Failed to schedule job '{job_id}': {e}")
        raise


async def remove_job(job_id: str):
    """Removes a scheduled job by its ID."""
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed scheduled job '{job_id}'.")
    except JobLookupError:
        logger.warning(f"Job '{job_id}' not found, cannot remove.")
    except Exception as e:
        logger.exception(f"Error removing job '{job_id}': {e}")
        # Don't re-raise usually, just log the error

async def reschedule_pending_digests():
    """Placeholder: Reschedules digests for users marked as pending."""
    # Requires persistent storage of conversation refs and access to adapter at startup.
    # This remains a complex problem to solve robustly.
    logger.warning("Rescheduling pending digests on startup is not fully implemented due to context limitations.")
    # from memory import current_memory # Avoid top-level import
    # users_pending = await current_memory.find_keys_with_pattern("digest_pending_schedule:*") # Needs specific memory implementation
    # if users_pending: logger.info(f"Found {len(users_pending)} users with pending digest schedules.")
    # Logic to fetch refs/adapter and call _update_digest_schedule would go here. 