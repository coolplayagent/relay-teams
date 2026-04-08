# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread
from typing import cast
from unittest.mock import patch

import pytest
from pydantic import JsonValue

from relay_teams.logger import (
    configure_logging,
    get_logger,
    log_event,
    log_model_output,
    log_tool_error,
    shutdown_logging,
)
from relay_teams.logger import logger as logger_module
from relay_teams.logger.logger import _WindowsSafeTimedRotatingFileHandler
from relay_teams.trace import bind_trace_context, trace_span


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


def test_httpx_info_access_logs_are_excluded_from_backend_log(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.info('HTTP Request: POST https://example.test "HTTP/1.1 200 OK"')

        shutdown_logging()

        backend_lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        debug_lines = (config_dir / "log" / "debug.log").read_text(encoding="utf-8")
        assert (
            'HTTP Request: POST https://example.test "HTTP/1.1 200 OK"'
            not in backend_lines
        )
        assert (
            'HTTP Request: POST https://example.test "HTTP/1.1 200 OK"' in debug_lines
        )
    finally:
        snapshot.restore()


def test_httpx_error_logs_remain_in_backend_log(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.error("HTTP transport failed")

        shutdown_logging()

        backend_lines = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        debug_lines = (config_dir / "log" / "debug.log").read_text(encoding="utf-8")
        assert "HTTP transport failed" in backend_lines
        assert "HTTP transport failed" in debug_lines
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

    with patch("relay_teams.logger.logger.sys") as mock_sys:
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

    with patch("relay_teams.logger.logger.sys") as mock_sys:
        mock_sys.platform = "win32"
        handler.rotate(str(log_file), str(dest))

    assert dest.read_text(encoding="utf-8") == "new content"
    assert log_file.read_text(encoding="utf-8") == ""


def test_default_redaction_masks_sensitive_message_and_payload_values(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.redaction")
        log_event(
            logger,
            logging.INFO,
            event="unit.redaction",
            message="structured redaction",
            payload={
                "authorization": "Bearer test-token",
                "client_secret": "top-secret",
                "url": "https://user:pass@example.test/path?api_key=query-secret",
                "safe": "ok",
            },
        )
        logger.error(
            "Authorization: Bearer direct-token Cookie: sessionid=abc123; csrftoken=def456 https://user:pass@example.test/path?api_key=query-secret"
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert '"authorization": "***"' in backend_text
        assert '"client_secret": "***"' in backend_text
        assert "Bearer ***" in backend_text
        assert "Cookie: sessionid=abc123; csrftoken=def456" in backend_text
        assert "example.test/path?api_key=" in backend_text
        assert "direct-token" not in backend_text
        assert "abc123" in backend_text
        assert "def456" in backend_text
        assert "query-secret" not in backend_text
        assert "user:pass@" not in backend_text
        assert '"safe": "ok"' in backend_text
    finally:
        snapshot.restore()


def test_nested_payload_redaction_masks_multi_level_maps(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.nested.redaction")
        nested_payload: JsonValue = {
            "provider": {
                "auth": {
                    "api_key": "nested-api-key",
                    "client_secret": "nested-client-secret",
                },
                "endpoints": [
                    {
                        "url": "https://user:pass@example.test/path?token=query-token",
                    },
                    {
                        "safe": "visible",
                    },
                ],
                "tuple_payload": [
                    {"authorization": "Bearer tuple-token"},
                    "sk-tuple-secret",
                ],
            }
        }
        log_event(
            logger,
            logging.INFO,
            event="unit.nested.redaction",
            message="nested payload redaction",
            payload=nested_payload,
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert '"api_key": "***"' in backend_text
        assert '"client_secret": "***"' in backend_text
        assert '"authorization": "***"' in backend_text
        assert "example.test/path?token=" in backend_text
        assert '"safe": "visible"' in backend_text
        assert "nested-api-key" not in backend_text
        assert "nested-client-secret" not in backend_text
        assert "tuple-token" not in backend_text
        assert "query-token" not in backend_text
        assert "sk-tuple-secret" not in backend_text
        assert "user:pass@" not in backend_text
    finally:
        snapshot.restore()


def test_log_model_output_and_tool_error_redact_sensitive_content(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        log_model_output(
            "role-1",
            "Authorization: Bearer model-token https://user:pass@example.test/path?api_key=query-secret",
        )
        log_tool_error(
            "role-1",
            '{"Authorization": "Bearer tool-token", "url": "https://user:pass@example.test/path?api_key=query-secret"}',
        )

        shutdown_logging()

        debug_text = (config_dir / "log" / "debug.log").read_text(encoding="utf-8")
        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "model-token" not in debug_text
        assert "tool-token" not in backend_text
        assert "query-secret" not in debug_text
        assert "query-secret" not in backend_text
        assert "example.test/path?api_key=" in debug_text
        assert "example.test/path?api_key=" in backend_text
    finally:
        snapshot.restore()


def test_exception_error_detail_is_redacted(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.exception")
        try:
            raise RuntimeError(
                "failed calling https://user:pass@example.test/path?api_key=query-secret with sk-secret-token"
            )
        except RuntimeError:
            logger.exception("request failed")

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "request failed" in backend_text
        assert "query-secret" not in backend_text
        assert "sk-secret-token" not in backend_text
        assert "user:pass@" not in backend_text
        assert "example.test/path?api_key=" in backend_text
    finally:
        snapshot.restore()


def test_log_event_snapshots_payload_before_async_formatting(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.payload.snapshot")
        payload: JsonValue = {
            "safe": "before",
            "nested": {"safe": "inner-before"},
            "items": ["before"],
        }
        log_event(
            logger,
            logging.INFO,
            event="unit.payload.snapshot",
            message="payload snapshot",
            payload=payload,
        )

        payload_dict = cast(dict[str, JsonValue], payload)
        payload_dict["safe"] = "after"
        nested_payload = cast(dict[str, JsonValue], payload_dict["nested"])
        nested_payload["safe"] = "inner-after"
        items_payload = cast(list[JsonValue], payload_dict["items"])
        items_payload[0] = "after"
        payload_dict["secret"] = "mutated-secret"

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert '"safe": "before"' in backend_text
        assert '"safe": "inner-before"' in backend_text
        assert '"items": ["before"]' in backend_text
        assert "mutated-secret" not in backend_text
        assert '"safe": "after"' not in backend_text
        assert '"safe": "inner-after"' not in backend_text
        assert '"items": ["after"]' not in backend_text
    finally:
        snapshot.restore()


def test_malformed_url_redaction_falls_back_without_dropping_log(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.malformed.url")
        logger.error(
            "request failed for http://[::1?token=query-secret but message still logs"
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "request failed for" in backend_text
        assert "but message still logs" in backend_text
        assert "http://[::1?token=query-secret" not in backend_text
        assert "query-secret" not in backend_text
        assert "***" in backend_text
    finally:
        snapshot.restore()


def test_redaction_keys_add_env_masks_custom_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv(
        "AGENT_TEAMS_LOG_REDACTION_KEYS_ADD",
        '["webhook_signature"]',
    )
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.keys.add")
        log_event(
            logger,
            logging.INFO,
            event="unit.keys.add",
            message="custom key redaction",
            payload={"webhook_signature": "sig-12345", "safe": "ok"},
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert '"webhook_signature": "***"' in backend_text
        assert "sig-12345" not in backend_text
        assert '"safe": "ok"' in backend_text
    finally:
        snapshot.restore()


def test_redaction_keys_replace_env_overrides_default_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv(
        "AGENT_TEAMS_LOG_REDACTION_KEYS_REPLACE",
        '["webhook_signature"]',
    )
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.keys.replace")
        log_event(
            logger,
            logging.INFO,
            event="unit.keys.replace",
            message="replace key redaction",
            payload={"secret": "plainvalue", "webhook_signature": "sig-67890"},
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert '"secret": "plainvalue"' in backend_text
        assert '"webhook_signature": "***"' in backend_text
        assert "sig-67890" not in backend_text
    finally:
        snapshot.restore()


def test_redaction_patterns_add_env_masks_custom_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv(
        "AGENT_TEAMS_LOG_REDACTION_PATTERNS_ADD",
        '["CUST-[A-Z0-9]{6,}"]',
    )
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.patterns.add")
        logger.error("custom token CUST-ABC123XYZ")

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "CUST-ABC123XYZ" not in backend_text
        assert "***" in backend_text
    finally:
        snapshot.restore()


def test_redaction_patterns_replace_env_overrides_default_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv(
        "AGENT_TEAMS_LOG_REDACTION_PATTERNS_REPLACE",
        '["CUST-[A-Z0-9]{6,}"]',
    )
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.patterns.replace")
        logger.error("Bearer visible-token CUST-ABC123XYZ")

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "Bearer visible-token" in backend_text
        assert "CUST-ABC123XYZ" not in backend_text
    finally:
        snapshot.restore()


def test_placeholder_is_treated_literally_for_default_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv("AGENT_TEAMS_LOG_REDACTION_PLACEHOLDER", r"\1")
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.placeholder.default")
        logger.error("secret sk-test-token")

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "sk-test-token" not in backend_text
        assert r"\1" in backend_text
    finally:
        snapshot.restore()


def test_placeholder_is_treated_literally_for_custom_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv("AGENT_TEAMS_LOG_REDACTION_PLACEHOLDER", "\\")
    monkeypatch.setenv(
        "AGENT_TEAMS_LOG_REDACTION_PATTERNS_ADD",
        '["CUST-[A-Z0-9]{6,}"]',
    )
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.placeholder.custom")
        logger.error("custom token CUST-ABC123XYZ")

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "CUST-ABC123XYZ" not in backend_text
        assert "\\" in backend_text
    finally:
        snapshot.restore()


def test_invalid_redaction_config_falls_back_to_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    snapshot = _RootLoggerSnapshot.take()
    monkeypatch.setenv("AGENT_TEAMS_LOG_REDACTION_KEYS_REPLACE", "not-json")
    try:
        configure_logging(config_dir=config_dir)
        logger = get_logger("tests.unit.invalid.redaction")
        log_event(
            logger,
            logging.INFO,
            event="unit.invalid.redaction",
            message="fallback redaction",
            payload={"secret": "fallback-secret"},
        )

        shutdown_logging()

        backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
        assert "fallback-secret" not in backend_text
        assert "Invalid AGENT_TEAMS_LOG_REDACTION_KEYS_REPLACE" in backend_text
    finally:
        snapshot.restore()


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
