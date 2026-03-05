from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import cast

import pytest

from agent_teams.core.types import JsonObject
from agent_teams.logger import (
    configure_logging,
    get_logger,
    log_event,
    log_tool_call,
)
from agent_teams.runtime.trace import bind_trace_context


def test_configure_logging_loads_ini_and_builds_rotating_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_TEAMS_LOG_PERSIST", "0")
    _snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=tmp_path)

        config_path = tmp_path / "logger.ini"
        log_path = tmp_path / "logs" / "agent_teams.log"
        assert config_path.exists()
        assert log_path.parent.exists()

        root = logging.getLogger()
        rotating_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, TimedRotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1
        assert rotating_handlers[0].backupCount == 14
    finally:
        _snapshot.restore()


def test_log_event_writes_json_log_with_trace_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_TEAMS_LOG_PERSIST", "0")
    _snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=tmp_path)
        logger = get_logger("tests.unit.logger")

        with bind_trace_context(trace_id="trace-1", request_id="req-1"):
            log_event(
                logger,
                logging.INFO,
                event="unit.test",
                message="logger test",
                payload={"secret": "Bearer test-token", "values": ["a", "b"]},
            )

        for handler in logging.getLogger().handlers:
            flush = getattr(handler, "flush", None)
            if callable(flush):
                _ = flush()

        log_path = tmp_path / "logs" / "agent_teams.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert lines
        payload = cast(JsonObject, json.loads(lines[-1]))
        assert payload["event"] == "unit.test"
        assert payload["trace_id"] == "trace-1"
        assert payload["request_id"] == "req-1"
        payload_field = payload.get("payload")
        assert isinstance(payload_field, dict)
        assert payload_field.get("secret") == "***"
        assert payload_field.get("values") == ["a", "b"]
    finally:
        _snapshot.restore()


def test_log_tool_call_writes_structured_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_TEAMS_LOG_PERSIST", "0")
    _snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=tmp_path)
        log_tool_call(
            "spec_coder",
            "read",
            {"path": "README.md"},
        )
        for handler in logging.getLogger().handlers:
            flush = getattr(handler, "flush", None)
            if callable(flush):
                _ = flush()

        log_path = tmp_path / "logs" / "agent_teams.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert lines
        payload = cast(JsonObject, json.loads(lines[-1]))
        assert payload["event"] == "tool.call.started"
        assert payload["message"] == "Tool call started"
        payload_field = payload.get("payload")
        assert isinstance(payload_field, dict)
        assert payload_field.get("role_id") == "spec_coder"
        assert payload_field.get("tool_name") == "read"
    finally:
        _snapshot.restore()


class _RootLoggerSnapshot:
    _handlers: tuple[logging.Handler, ...]
    _level: int

    def __init__(
        self,
        *,
        handlers: tuple[logging.Handler, ...],
        level: int,
    ) -> None:
        self._handlers = handlers
        self._level = level

    @classmethod
    def take(cls) -> _RootLoggerSnapshot:
        root = logging.getLogger()
        return cls(handlers=tuple(root.handlers), level=root.level)

    def restore(self) -> None:
        root = logging.getLogger()
        current_handlers = tuple(root.handlers)
        root.handlers.clear()
        for handler in self._handlers:
            root.addHandler(handler)
        root.setLevel(self._level)
        for handler in current_handlers:
            if handler not in self._handlers:
                handler.close()
