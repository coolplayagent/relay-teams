from __future__ import annotations

from threading import RLock


class HookRuntimeEnvStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._env_by_run_id: dict[str, dict[str, str]] = {}

    def replace(self, *, run_id: str, values: dict[str, str]) -> None:
        with self._lock:
            self._env_by_run_id[run_id] = dict(values)

    def get(self, run_id: str) -> dict[str, str]:
        with self._lock:
            values = self._env_by_run_id.get(run_id)
            if values is None:
                return {}
            return dict(values)

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._env_by_run_id.pop(run_id, None)
