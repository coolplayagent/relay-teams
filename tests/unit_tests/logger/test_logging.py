# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest

from agent_teams.logger import (
    configure_logging,
    get_logger,
    log_event,
    shutdown_logging,
)
from agent_teams.logger import logger as logger_module
from agent_teams.logger.logger import _WindowsSafeTimedRotatingFileHandler
from agent_teams.trace import bind_trace_context, trace_span


def test_configure_logging_creates_backend_debug_and_frontend_logs(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        shutdown_logging()

        assert (config_dir / "log" / "backend.log").exists()
        assert (config_dir / "log" / "debug.log").exists()
        assert (config_dir / "log" / "frontend.log").exists()
    finally:
        snapshot.restore()


def test_configure_logging_uses_app_log_dir_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    log_dir = config_dir / "log"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setattr(logger_module, "get_app_config_dir", lambda: config_dir)
    try:
        configure_logging()
        shutdown_logging()

        assert log_dir.exists()
        assert (log_dir / "backend.log").exists()
        assert (log_dir / "debug.log").exists()
        assert (log_dir / "frontend.log").exists()
    finally:
        snapshot.restore()


def test_log_event_writes_human_readable_backend_log_with_trace_context(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.logger")

        with bind_trace_context(
            trace_id="trace-1",
            request_id="req-1",
            trigger_id="trigger-1",
        ):
            with trace_span(logger, component="logger.tests", operation="write_log"):
                log_event(
                    logger,
                    logging.INFO,
                    event="unit.test",
                    message="logger test",
                    payload={"secret": "Bearer test-token", "values": ["a", "b"]},
                )

        shutdown_logging()

        log_path = config_dir / "log" / "backend.log"
        debug_path = config_dir / "log" / "debug.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        matching_line = next(line for line in lines if "event=unit.test" in line)
        debug_lines = debug_path.read_text(encoding="utf-8").splitlines()
        assert " | INFO | backend | " in matching_line
        assert "trace_id=trace-1" in matching_line
        assert "request_id=req-1" in matching_line
        assert "trigger_id=trigger-1" in matching_line
        assert '"secret": "***"' in matching_line
        assert '"values": ["a", "b"]' in matching_line
        assert any("event=trace.span.succeeded" in line for line in debug_lines)
        assert not any("event=trace.span.succeeded" in line for line in lines)
    finally:
        snapshot.restore()


def test_frontend_logger_writes_only_frontend_file(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        frontend_logger = get_logger("tests.unit.frontend", source="frontend")
        log_event(
            frontend_logger,
            logging.ERROR,
            event="frontend.test",
            message="frontend failure",
            payload={"page": "chat"},
        )

        shutdown_logging()

        backend_lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        frontend_lines = (config_dir / "log" / "frontend.log").read_text(
            encoding="utf-8"
        )
        assert "event=frontend.test" not in backend_lines
        assert "event=frontend.test" in frontend_lines
        assert " | frontend | " in frontend_lines
    finally:
        snapshot.restore()


def test_debug_events_write_only_to_debug_log(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.debug")
        log_event(
            logger,
            logging.DEBUG,
            event="unit.debug",
            message="debug only",
        )

        shutdown_logging()

        backend_lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        debug_lines = (config_dir / "log" / "debug.log").read_text(encoding="utf-8")
        assert "event=unit.debug" not in backend_lines
        assert "event=unit.debug" in debug_lines
    finally:
        snapshot.restore()


def test_uvicorn_access_logs_are_excluded_from_backend_log(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.info('127.0.0.1 - "GET /api/system/health HTTP/1.1" 200')

        shutdown_logging()

        backend_lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        debug_lines = (config_dir / "log" / "debug.log").read_text(encoding="utf-8")
        assert "uvicorn.access" not in backend_lines
        assert "uvicorn.access" in debug_lines
    finally:
        snapshot.restore()


def test_log_level_filters_lower_priority_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv("AGENT_TEAMS_LOG_LEVEL", "WARNING")
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.filter")
        log_event(
            logger,
            logging.INFO,
            event="unit.info",
            message="ignore me",
        )
        log_event(
            logger,
            logging.WARNING,
            event="unit.warning",
            message="keep me",
        )

        shutdown_logging()

        lines = (
            (config_dir / "log" / "backend.log")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        joined = "\n".join(lines)
        assert "event=unit.info" not in joined
        assert "event=unit.warning" in joined
    finally:
        snapshot.restore()


def test_shutdown_logging_flushes_pending_events(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.flush")
        log_event(
            logger,
            logging.INFO,
            event="unit.flush",
            message="flush me",
        )

        shutdown_logging()

        lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "event=unit.flush" in lines
    finally:
        snapshot.restore()


def test_backend_logger_handles_concurrent_writes_without_losing_lines(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.concurrent")

        def worker(worker_id: int) -> None:
            for index in range(25):
                log_event(
                    logger,
                    logging.INFO,
                    event="unit.concurrent",
                    message=f"worker={worker_id} index={index}",
                )

        threads = [Thread(target=worker, args=(worker_id,)) for worker_id in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        shutdown_logging()

        lines = (
            (config_dir / "log" / "backend.log")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        matches = [line for line in lines if "event=unit.concurrent" in line]
        assert len(matches) == 100
    finally:
        snapshot.restore()


def test_windows_safe_handler_copies_and_truncates_on_windows(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("hello log", encoding="utf-8")
    handler = _WindowsSafeTimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        utc=True,
    )
    handler.close()

    dest = tmp_path / "test.log.2026-03-17"

    with patch("agent_teams.logger.logger.sys") as mock_sys:
        mock_sys.platform = "win32"
        handler.rotate(str(log_file), str(dest))

    assert dest.read_text(encoding="utf-8") == "hello log"
    assert log_file.read_text(encoding="utf-8") == ""


def test_windows_safe_handler_removes_existing_dest_before_copy(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("new content", encoding="utf-8")
    dest = tmp_path / "test.log.rotated"
    dest.write_text("old content", encoding="utf-8")
    handler = _WindowsSafeTimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        utc=True,
    )
    handler.close()

    with patch("agent_teams.logger.logger.sys") as mock_sys:
        mock_sys.platform = "win32"
        handler.rotate(str(log_file), str(dest))

    assert dest.read_text(encoding="utf-8") == "new content"
    assert log_file.read_text(encoding="utf-8") == ""


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
        shutdown_logging()
        root = logging.getLogger()
        current_handlers = tuple(root.handlers)
        root.handlers.clear()
        for handler in self._handlers:
            root.addHandler(handler)
        root.setLevel(self._level)
        for handler in current_handlers:
            if handler not in self._handlers:
                handler.close()
