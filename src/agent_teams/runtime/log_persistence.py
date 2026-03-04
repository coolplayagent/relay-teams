from __future__ import annotations

import json
import logging
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any

from agent_teams.state.log_repo import LogRepository


class PersistentLogHandler(logging.Handler):
    def __init__(
        self,
        *,
        db_path: Path,
        queue_size: int = 5000,
        flush_batch_size: int = 200,
        flush_interval_seconds: float = 0.3,
    ) -> None:
        super().__init__()
        self._repo = LogRepository(db_path=db_path)
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=queue_size)
        self._flush_batch_size = flush_batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._stop = Event()
        self._worker = Thread(target=self._run, name='log-persistence-worker', daemon=True)
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record)
            parsed: dict[str, Any]
            if isinstance(rendered, str):
                parsed = json.loads(rendered)
            else:
                parsed = {'message': str(rendered), 'level': record.levelname}
            self._queue.put_nowait(parsed)
        except Full:
            # Intentionally drop when saturated to avoid blocking business flows.
            return
        except Exception:
            return

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=1.0)
        self._drain_once(force=True)
        super().close()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain_once(force=False)

    def _drain_once(self, *, force: bool) -> None:
        rows: list[dict[str, Any]] = []
        timeout = 0.0 if force else self._flush_interval_seconds
        try:
            first = self._queue.get(timeout=timeout)
            rows.append(first)
        except Empty:
            return

        while len(rows) < self._flush_batch_size:
            try:
                rows.append(self._queue.get_nowait())
            except Empty:
                break

        self._repo.append_many(rows)
