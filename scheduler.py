"""
scheduler.py — APScheduler background jobs.
Install: pip install apscheduler
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()


async def _drain_queue(q: asyncio.Queue):
    """Consume messages from a queue until sentinel (None) to prevent memory leak."""
    while True:
        try:
            msg = await asyncio.wait_for(q.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        if msg is None:
            return
        if isinstance(msg, dict) and msg.get('type') in ('error', 'warning'):
            log.warning("Drained message: %s", msg.get('message', ''))


async def _run_auto_followups():
    from outreach import run_followups_web
    q: asyncio.Queue = asyncio.Queue()
    drain = asyncio.create_task(_drain_queue(q))
    try:
        await run_followups_web(q)
    except Exception as e:
        log.warning("auto_followups failed: %s", e)
    finally:
        # Signal the drainer to exit
        await q.put(None)
        try:
            await asyncio.wait_for(drain, timeout=10)
        except asyncio.TimeoutError:
            drain.cancel()


async def _run_auto_reply_scan():
    from outreach import check_replies
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, check_replies)
    except Exception as e:
        log.warning("auto_reply_scan failed: %s", e)


async def _run_daily_cleanup():
    from audit_pages import delete_expired_audit_pages
    from cache import cache_purge_expired
    try:
        delete_expired_audit_pages()
    except Exception as e:
        log.warning("audit cleanup failed: %s", e)
    try:
        cache_purge_expired()
    except Exception as e:
        log.warning("cache purge failed: %s", e)


async def _run_daily_warmup():
    from config import WARMUP_ENABLED
    if not WARMUP_ENABLED:
        return
    from outreach import run_warmup_cycle
    q: asyncio.Queue = asyncio.Queue()
    drain = asyncio.create_task(_drain_queue(q))
    try:
        await run_warmup_cycle(queue=q)
    except Exception as e:
        log.warning("daily_warmup failed: %s", e)
    finally:
        await q.put(None)
        try:
            await asyncio.wait_for(drain, timeout=10)
        except asyncio.TimeoutError:
            drain.cancel()


async def _run_fx_refresh():
    from audit import refresh_fx_rates_async
    try:
        ok = await refresh_fx_rates_async()
        if ok:
            log.info("FX rates refreshed")
        else:
            log.warning("FX rate refresh failed")
    except Exception as e:
        log.warning("fx_refresh failed: %s", e)


def start_scheduler():
    """Start the scheduler with configured jobs. Idempotent."""
    if scheduler.running:
        return scheduler

    scheduler.add_job(
        _run_auto_followups,
        CronTrigger(minute="*/15"),
        id="auto_followups",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_auto_reply_scan,
        CronTrigger(hour="*/2"),
        id="auto_reply_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_daily_cleanup,
        CronTrigger(hour=3, minute=0),
        id="daily_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_daily_warmup,
        CronTrigger(hour=6, minute=0),
        id="daily_warmup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_fx_refresh,
        CronTrigger(hour=0, minute=0),
        id="fx_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    print("[OK] Scheduler started with 5 background jobs")
    return scheduler


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[STOP] Scheduler stopped")


def setup_scheduler(app):
    """Legacy compatibility: call start_scheduler."""
    return start_scheduler()
