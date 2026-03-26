"""Activity logging utilities with batching support."""

from __future__ import annotations

import atexit
import threading
from typing import List, Optional

from flask import current_app
from flask_login import current_user

from app.models import ActivityLog, db


class _ActivityLogger:
    """Internal helper that buffers and flushes activity logs."""

    def __init__(
        self, app, flush_interval: float = 0.1, batch_size: int = 20
    ) -> None:
        self.app = app
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self._queue: List[ActivityLog] = []
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        atexit.register(self.flush)

    # ------------------------------------------------------------------
    def log(self, activity: str, user_id: Optional[int]) -> None:
        entry = ActivityLog(user_id=user_id, activity=activity)
        with self._lock:
            self._queue.append(entry)
            if len(self._queue) >= self.batch_size:
                self._flush_unlocked()
            else:
                self._start_timer_unlocked()

    # ------------------------------------------------------------------
    def _start_timer_unlocked(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.flush_interval, self.flush)
        self._timer.daemon = True
        self._timer.start()

    # ------------------------------------------------------------------
    def _flush_unlocked(self) -> None:
        logs = list(self._queue)
        self._queue.clear()
        if self._timer:
            self._timer.cancel()
            self._timer = None
        try:
            with self.app.app_context():
                db.session.bulk_save_objects(logs)
                db.session.commit()
        except Exception:
            # Swallow errors to avoid crashing the application during shutdown
            pass

    # ------------------------------------------------------------------
    def flush(self) -> None:
        with self._lock:
            if not self._queue:
                if self._timer:
                    self._timer = None
                return
            self._flush_unlocked()


# ----------------------------------------------------------------------
def _get_logger() -> _ActivityLogger:
    app = current_app._get_current_object()
    logger = app.extensions.get("activity_logger")
    if logger is None:
        logger = app.extensions["activity_logger"] = _ActivityLogger(app)
    return logger


def flush_activity_logs() -> None:
    """Public helper to force flush of any pending activity logs."""
    logger = current_app.extensions.get("activity_logger")
    if logger:
        logger.flush()


def log_activity(activity: str, user_id: Optional[int] = None) -> None:
    """Record an activity performed by a user.

    Existing call sites remain unchanged.  Logs are buffered and committed
    asynchronously in batches.
    """
    if user_id is None:
        if current_user and not current_user.is_anonymous:
            user_id = current_user.id

    logger = _get_logger()
    logger.log(activity, user_id)
