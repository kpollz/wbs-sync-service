"""APScheduler driver: runs the pipeline on a fixed interval."""

from __future__ import annotations

import logging
import signal

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import get_settings
from .pipeline import run_once

log = logging.getLogger(__name__)

_JOB_ID = "wbs-sync"


def _safe_run_once() -> None:
    """Run the pipeline, never raising into the scheduler (keeps it alive)."""
    try:
        run_once()
    except Exception:
        log.exception("scheduled sync run failed")


def run_scheduler() -> None:
    cfg = get_settings()

    if cfg.sync_run_on_start:
        log.info("running an initial sync on start")
        _safe_run_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        _safe_run_once,
        "interval",
        hours=cfg.sync_interval_hours,
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
    )

    def _shutdown(signum, _frame):  # noqa: ANN001
        log.info("received signal %s, shutting down", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info(
        "scheduler started: interval=%s hours (PID will be kept alive by Docker)",
        cfg.sync_interval_hours,
    )
    scheduler.start()
