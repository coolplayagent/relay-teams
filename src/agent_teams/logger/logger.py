# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import copy
import configparser
import json
import logging
import os
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from pathlib import Path
from queue import SimpleQueue
from threading import Lock
from types import TracebackType
from typing import Literal, cast, override

from agent_teams.builtin import (
    get_builtin_logger_ini_path,
)
from agent_teams.env import load_merged_env_vars
from agent_teams.paths import get_app_config_dir

from agent_teams.trace import get_trace_context

SERVICE_NAME = "agent_teams"
BACKEND_LOGGER_NAMESPACE = "agent_teams.backend"
FRONTEND_LOGGER_NAMESPACE = "agent_teams.frontend"
DEFAULT_BACKEND_LOG_FILENAME = "backend.log"
DEFAULT_DEBUG_LOG_FILENAME = "debug.log"
DEFAULT_FRONTEND_LOG_FILENAME = "frontend.log"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_DEBUG_LOG_LEVEL = "DEBUG"
DEFAULT_LOG_CONSOLE = "1"
DEFAULT_BACKUP_COUNT = 14
_LOGGER_INI_NAME = "logger.ini"

_RUNTIME_ENV_VALUES: dict[str, str] | None = None
_LOGGING_LOCK = Lock()
_LOGGING_RUNTIME: "_LoggingRuntime | None" = None
_DEFAULT_RECORD_FACTORY = logging.getLogRecordFactory()

type LogSource = Literal["backend", "frontend"]
type LogExcInfo = (
    bool
    | BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
    | None
)


def _trace_log_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _DEFAULT_RECORD_FACTORY(*args, **kwargs)
    context = get_trace_context()
    context_fields = {
        "trace_id": context.trace_id,
        "request_id": context.request_id,
        "session_id": context.session_id,
        "run_id": context.run_id,
        "task_id": context.task_id,
        "trigger_id": context.trigger_id,
        "instance_id": context.instance_id,
        "role_id": context.role_id,
        "tool_call_id": context.tool_call_id,
        "span_id": context.span_id,
        "parent_span_id": context.parent_span_id,
    }
    for field_name, field_value in context_fields.items():
        if field_value is not None and not hasattr(record, field_name):
            setattr(record, field_name, field_value)
    return record


logging.setLogRecordFactory(_trace_log_record_factory)


class _LoggingRuntime:
    def __init__(
        self,
        *,
        backend_listener: QueueListener,
        frontend_listener: QueueListener,
        backend_queue_handler: QueueHandler,
        frontend_queue_handler: QueueHandler,
        managed_handlers: tuple[logging.Handler, ...],
    ) -> None:
        self.backend_listener = backend_listener
        self.frontend_listener = frontend_listener
        self.backend_queue_handler = backend_queue_handler
        self.frontend_queue_handler = frontend_queue_handler
        self.managed_handlers = managed_handlers


class StructuredQueueHandler(QueueHandler):
    @override
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = cast(logging.LogRecord, copy.copy(record))
        prepared.message = prepared.getMessage()
        prepared.msg = prepared.message
        prepared.args = None
        if prepared.exc_info:
            prepared.error_detail = _build_error_payload(prepared.exc_info)
        prepared.exc_info = None
        prepared.exc_text = None
        prepared.stack_info = None
        return prepared


class _BackendLogFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if _resolve_log_source(record) != "backend":
            return False
        return record.name != "uvicorn.access"


class _DebugLogFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        return _resolve_log_source(record) == "backend"


class HumanReadableFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(UTC).isoformat()
        source = _resolve_log_source(record)
        logger_name = _display_logger_name(record.name, source)
        event = str(getattr(record, "event", "-") or "-")
        message = record.getMessage()

        parts = [
            timestamp,
            record.levelname,
            source,
            logger_name,
            f"event={event}",
        ]

        for key in (
            "trace_id",
            "request_id",
            "session_id",
            "run_id",
            "task_id",
            "trigger_id",
            "instance_id",
            "role_id",
            "tool_call_id",
            "span_id",
            "parent_span_id",
        ):
            value = getattr(record, key, None)
            if value:
                parts.append(f"{key}={value}")

        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            parts.append(f"duration_ms={duration_ms}")

        parts.append(f"message={message}")

        payload = getattr(record, "payload", None)
        if payload:
            parts.append(f"payload={_render_payload(payload)}")

        error_detail = getattr(record, "error_detail", None)
        if error_detail is not None:
            parts.append(f"error={_render_payload(error_detail)}")

        return " | ".join(parts)


def configure_logging(*, config_dir: Path | None = None) -> None:
    global _LOGGING_RUNTIME
    with _LOGGING_LOCK:
        shutdown_logging()
        _refresh_runtime_env_values()

        resolved_config_dir = (
            get_app_config_dir()
            if config_dir is None
            else config_dir.expanduser().resolve()
        )
        resolved_config_dir.mkdir(parents=True, exist_ok=True)
        log_dir = resolved_config_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger_settings = _load_logger_settings(resolved_config_dir)

        backend_level = _resolve_log_level(
            env_key="AGENT_TEAMS_LOG_BACKEND_LEVEL",
            fallback_key="AGENT_TEAMS_LOG_LEVEL",
            default_name=logger_settings.backend_level,
        )
        frontend_level = _resolve_log_level(
            env_key="AGENT_TEAMS_LOG_FRONTEND_LEVEL",
            fallback_key="AGENT_TEAMS_LOG_LEVEL",
            default_name=logger_settings.frontend_level,
        )
        debug_level = _resolve_debug_log_level(default_name=logger_settings.debug_level)

        backend_formatter = HumanReadableFormatter()
        debug_formatter = HumanReadableFormatter()
        frontend_formatter = HumanReadableFormatter()

        backend_file_handler = _build_file_handler(
            path=log_dir / DEFAULT_BACKEND_LOG_FILENAME,
            level=backend_level,
            formatter=backend_formatter,
        )
        backend_file_handler.addFilter(_BackendLogFilter())
        debug_file_handler = _build_file_handler(
            path=log_dir / DEFAULT_DEBUG_LOG_FILENAME,
            level=debug_level,
            formatter=debug_formatter,
        )
        debug_file_handler.addFilter(_DebugLogFilter())
        frontend_file_handler = _build_file_handler(
            path=log_dir / DEFAULT_FRONTEND_LOG_FILENAME,
            level=frontend_level,
            formatter=frontend_formatter,
        )

        console_handler: logging.Handler | None = None
        if _console_enabled(default_enabled=logger_settings.console_enabled):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(backend_level)
            console_handler.setFormatter(backend_formatter)
            console_handler.addFilter(_BackendLogFilter())

        backend_queue: SimpleQueue[logging.LogRecord] = SimpleQueue()
        frontend_queue: SimpleQueue[logging.LogRecord] = SimpleQueue()
        backend_queue_handler = StructuredQueueHandler(backend_queue)
        frontend_queue_handler = StructuredQueueHandler(frontend_queue)
        backend_queue_handler.setLevel(logging.DEBUG)
        frontend_queue_handler.setLevel(logging.DEBUG)

        backend_targets: list[logging.Handler] = [
            backend_file_handler,
            debug_file_handler,
        ]
        if console_handler is not None:
            backend_targets.append(console_handler)

        backend_listener = QueueListener(
            backend_queue,
            *backend_targets,
            respect_handler_level=True,
        )
        frontend_listener = QueueListener(
            frontend_queue,
            frontend_file_handler,
            respect_handler_level=True,
        )
        backend_listener.start()
        frontend_listener.start()

        root = logging.getLogger()
        _reset_logger_handlers(root)
        root.setLevel(logging.DEBUG)
        root.addHandler(backend_queue_handler)

        backend_root = logging.getLogger(BACKEND_LOGGER_NAMESPACE)
        _reset_logger_handlers(backend_root)
        backend_root.setLevel(logging.DEBUG)
        backend_root.propagate = True

        frontend_root = logging.getLogger(FRONTEND_LOGGER_NAMESPACE)
        _reset_logger_handlers(frontend_root)
        frontend_root.setLevel(logging.DEBUG)
        frontend_root.propagate = False
        frontend_root.addHandler(frontend_queue_handler)

        _configure_uvicorn_loggers()

        managed_handlers: list[logging.Handler] = [
            backend_queue_handler,
            frontend_queue_handler,
            backend_file_handler,
            debug_file_handler,
            frontend_file_handler,
        ]
        if console_handler is not None:
            managed_handlers.append(console_handler)

        _LOGGING_RUNTIME = _LoggingRuntime(
            backend_listener=backend_listener,
            frontend_listener=frontend_listener,
            backend_queue_handler=backend_queue_handler,
            frontend_queue_handler=frontend_queue_handler,
            managed_handlers=tuple(managed_handlers),
        )


def shutdown_logging() -> None:
    global _LOGGING_RUNTIME
    runtime = _LOGGING_RUNTIME
    if runtime is None:
        return

    root = logging.getLogger()
    frontend_root = logging.getLogger(FRONTEND_LOGGER_NAMESPACE)
    backend_root = logging.getLogger(BACKEND_LOGGER_NAMESPACE)

    _remove_handler(root, runtime.backend_queue_handler)
    _remove_handler(frontend_root, runtime.frontend_queue_handler)
    _remove_handler(backend_root, runtime.backend_queue_handler)

    runtime.backend_listener.stop()
    runtime.frontend_listener.stop()

    for handler in runtime.managed_handlers:
        handler.close()

    _LOGGING_RUNTIME = None


def get_logger(name: str, *, source: LogSource = "backend") -> logging.Logger:
    namespace = (
        BACKEND_LOGGER_NAMESPACE if source == "backend" else FRONTEND_LOGGER_NAMESPACE
    )
    normalized_name = name.strip().replace(" ", "_")
    if normalized_name.startswith(f"{namespace}."):
        logger_name = normalized_name
    elif normalized_name.startswith("agent_teams."):
        logger_name = f"{namespace}.{normalized_name[len('agent_teams.') :]}"
    else:
        logger_name = f"{namespace}.{normalized_name}"
    return logging.getLogger(logger_name)


def close_model_stream() -> None:
    return


def log_model_output(role_id: str, message: str) -> None:
    logger = get_logger(__name__)
    log_event(
        logger,
        logging.DEBUG,
        event="model.output",
        message="Model output emitted",
        payload={"role_id": role_id, "output": _safe_json(message)},
    )


def log_tool_call(role_id: str, tool_name: str, params: dict[str, JsonValue]) -> None:
    logger = get_logger(__name__)
    short = _safe_json(params)
    log_event(
        logger,
        logging.DEBUG,
        event="tool.call.started",
        message="Tool call started",
        payload={"role_id": role_id, "tool_name": tool_name, "params": short},
    )


def log_tool_error(role_id: str, payload: str) -> None:
    logger = get_logger(__name__)
    log_event(
        logger,
        logging.ERROR,
        event="tool.call.failed",
        message="Tool call failed",
        payload={"role_id": role_id, "detail": payload},
    )


def log_model_stream_chunk(role_id: str, text: str) -> None:
    _ = (role_id, text)
    return


def log_event(
    logger: logging.Logger,
    level: int,
    *,
    event: str,
    message: str,
    payload: dict[str, JsonValue] | None = None,
    duration_ms: int | None = None,
    exc_info: LogExcInfo = None,
) -> None:
    logger.log(
        level,
        message,
        extra={
            "event": event,
            "payload": sanitize_payload(payload or {}),
            "duration_ms": duration_ms,
        },
        exc_info=exc_info,
    )


def sanitize_payload(payload: JsonValue) -> JsonValue:
    if isinstance(payload, dict):
        dict_payload = cast(dict[object, JsonValue], payload)
        return {
            str(key): sanitize_payload(value) for key, value in dict_payload.items()
        }
    if isinstance(payload, list):
        list_payload = cast(list[JsonValue], payload)
        return [sanitize_payload(value) for value in list_payload]
    if isinstance(payload, tuple):
        tuple_payload = cast(tuple[JsonValue, ...], payload)
        return [sanitize_payload(value) for value in tuple_payload]
    if isinstance(payload, str):
        return _truncate(_mask_sensitive(payload))
    return payload


def _refresh_runtime_env_values() -> None:
    global _RUNTIME_ENV_VALUES
    _RUNTIME_ENV_VALUES = load_merged_env_vars()


def _get_runtime_env_value(key: str, default: str) -> str:
    values = _RUNTIME_ENV_VALUES
    if values is None:
        _refresh_runtime_env_values()
        values = _RUNTIME_ENV_VALUES
    if values is None:
        return default
    return values.get(key, default)


class _LoggerSettings:
    def __init__(
        self,
        *,
        backend_level: str | None = None,
        frontend_level: str | None = None,
        debug_level: str | None = None,
        console_enabled: bool | None = None,
    ) -> None:
        self.backend_level = backend_level
        self.frontend_level = frontend_level
        self.debug_level = debug_level
        self.console_enabled = console_enabled


def _console_enabled(default_enabled: bool | None = None) -> bool:
    raw = _get_runtime_env_value("AGENT_TEAMS_LOG_CONSOLE", DEFAULT_LOG_CONSOLE)
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    if default_enabled is not None:
        return default_enabled
    return DEFAULT_LOG_CONSOLE.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_log_level(
    *,
    env_key: str,
    fallback_key: str,
    default_name: str | None = None,
) -> int:
    level_name = _get_runtime_env_value(
        env_key,
        _get_runtime_env_value(
            fallback_key,
            default_name or DEFAULT_LOG_LEVEL,
        ),
    )
    resolved = getattr(logging, level_name.strip().upper(), logging.INFO)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _resolve_debug_log_level(*, default_name: str | None = None) -> int:
    level_name = _get_runtime_env_value(
        "AGENT_TEAMS_LOG_DEBUG_LEVEL",
        default_name or DEFAULT_DEBUG_LOG_LEVEL,
    )
    resolved = getattr(logging, level_name.strip().upper(), logging.DEBUG)
    if isinstance(resolved, int):
        return resolved
    return logging.DEBUG


def _load_logger_settings(config_dir: Path) -> _LoggerSettings:
    logger_ini_path = config_dir / _LOGGER_INI_NAME
    if not logger_ini_path.exists():
        logger_ini_path = get_builtin_logger_ini_path()
    parser = configparser.ConfigParser()
    try:
        with logger_ini_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except Exception as exc:
        raise RuntimeError(f"Failed to load logger.ini: {exc}") from exc

    if not parser.has_section("agent_teams"):
        return _LoggerSettings()

    section = parser["agent_teams"]
    console_enabled: bool | None = None
    if "console" in section:
        console_enabled = section.getboolean("console", fallback=True)
    return _LoggerSettings(
        backend_level=section.get("backend_level"),
        frontend_level=section.get("frontend_level"),
        debug_level=section.get("debug_level"),
        console_enabled=console_enabled,
    )


class _WindowsSafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler with Windows-safe rotation.

    On Windows, os.rename raises PermissionError (WinError 32) when the source
    file is still held open (e.g., by antivirus or OS buffering). This subclass
    overrides rotate() with a copy-then-truncate strategy: the content is copied
    to the destination and the source is cleared in-place, so the open stream
    handle is never invalidated.
    """

    def rotate(self, source: str, dest: str) -> None:
        if sys.platform != "win32" or callable(self.rotator):
            super().rotate(source, dest)
            return
        if os.path.exists(dest):
            os.remove(dest)
        shutil.copy2(source, dest)
        with open(source, "w", encoding="utf-8"):
            pass


def _build_file_handler(
    *,
    path: Path,
    level: int,
    formatter: logging.Formatter,
) -> _WindowsSafeTimedRotatingFileHandler:
    handler = _WindowsSafeTimedRotatingFileHandler(
        filename=str(path),
        when="midnight",
        interval=1,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
        utc=True,
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _build_error_payload(exc_info: LogExcInfo) -> dict[str, JsonValue]:
    exc_type: type[BaseException] | None
    exc_value: BaseException | None
    exc_tb: TracebackType | None

    if isinstance(exc_info, BaseException):
        exc_type = type(exc_info)
        exc_value = exc_info
        exc_tb = exc_info.__traceback__
    elif isinstance(exc_info, tuple):
        exc_type = cast(type[BaseException] | None, exc_info[0])
        exc_value = cast(BaseException | None, exc_info[1])
        exc_tb = cast(TracebackType | None, exc_info[2])
    else:
        exc_type = None
        exc_value = None
        exc_tb = None

    return {
        "type": exc_type.__name__ if exc_type is not None else "Exception",
        "message": str(exc_value) if exc_value is not None else "",
        "stack": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    }


def _resolve_log_source(record: logging.LogRecord) -> LogSource:
    explicit = getattr(record, "source", None)
    if explicit == "frontend":
        return "frontend"
    if record.name.startswith(FRONTEND_LOGGER_NAMESPACE):
        return "frontend"
    return "backend"


def _display_logger_name(name: str, source: LogSource) -> str:
    prefix = (
        f"{BACKEND_LOGGER_NAMESPACE}."
        if source == "backend"
        else f"{FRONTEND_LOGGER_NAMESPACE}."
    )
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def _render_payload(payload: object) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        text = str(payload)
    return _truncate(text, limit=500)


def _reset_logger_handlers(logger: logging.Logger) -> None:
    handlers = tuple(logger.handlers)
    logger.handlers.clear()
    for handler in handlers:
        handler.close()


def _remove_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    if handler in logger.handlers:
        logger.removeHandler(handler)


def _configure_uvicorn_loggers() -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.DEBUG)


def _mask_sensitive(value: str) -> str:
    lowered = value.lower()
    if "sk-" in lowered or "bearer " in lowered:
        return "***"
    return value


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def _safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 300:
        return text[:300] + "...(truncated)"
    return text
