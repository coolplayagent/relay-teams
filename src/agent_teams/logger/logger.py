from __future__ import annotations

import ast
import json
import logging
import logging.config
import os
import traceback
from configparser import ConfigParser
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import cast, override

from agent_teams.core.types import JsonObject, JsonValue
from agent_teams.logger.log_persistence import PersistentLogHandler
from agent_teams.runtime.trace import get_trace_context

SERVICE_NAME = "agent_teams"
LOGGER_CONFIG_FILENAME = "logger.ini"
DEFAULT_LOG_FILENAME = "agent_teams.log"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "json"

type LogExcInfo = (
    bool
    | BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
    | None
)

DEFAULT_LOGGER_INI = """[loggers]
keys=root

[handlers]
keys=consoleHandler,rotatingFileHandler

[formatters]
keys=jsonFormatter,consoleFormatter

[logger_root]
level=INFO
handlers=consoleHandler,rotatingFileHandler

[handler_consoleHandler]
class=StreamHandler
level=INFO
formatter=jsonFormatter
args=(sys.stdout,)

[handler_rotatingFileHandler]
class=handlers.TimedRotatingFileHandler
level=INFO
formatter=jsonFormatter
args=('%(log_file)s', 'midnight', 1, 14, 'utf-8', False, True)

[formatter_jsonFormatter]
class=agent_teams.logger.JsonFormatter

[formatter_consoleFormatter]
format=[%(asctime)s] %(levelname)s %(name)s: %(message)s
datefmt=%Y-%m-%d %H:%M:%S
"""


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: JsonObject = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "env": os.getenv("AGENT_TEAMS_ENV", "dev"),
            "logger": record.name,
            "message": record.getMessage(),
        }

        context = get_trace_context()
        if context.trace_id:
            payload["trace_id"] = context.trace_id
        if context.request_id:
            payload["request_id"] = context.request_id
        if context.session_id:
            payload["session_id"] = context.session_id
        if context.run_id:
            payload["run_id"] = context.run_id
        if context.task_id:
            payload["task_id"] = context.task_id
        if context.instance_id:
            payload["instance_id"] = context.instance_id
        if context.role_id:
            payload["role_id"] = context.role_id
        if context.tool_call_id:
            payload["tool_call_id"] = context.tool_call_id

        event = getattr(record, "event", None)
        if event:
            payload["event"] = event

        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms

        log_payload = cast(JsonValue | None, getattr(record, "payload", None))
        if log_payload is not None:
            payload["payload"] = sanitize_payload(log_payload)

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload["error"] = {
                "type": exc_type.__name__ if exc_type else "Exception",
                "message": str(exc_value),
                "stack": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ),
            }

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    *, config_dir: Path | None = None, persist_db_path: Path | None = None
) -> None:
    resolved_config_dir = (config_dir or Path(".agent_teams")).resolve()
    resolved_config_dir.mkdir(parents=True, exist_ok=True)

    config_path = resolved_config_dir / LOGGER_CONFIG_FILENAME
    _ensure_logger_ini_exists(config_path)
    _ensure_file_handler_directories(
        config_path=config_path, config_dir=resolved_config_dir
    )

    logging.config.fileConfig(
        config_path,
        defaults=_file_config_defaults(resolved_config_dir),
        disable_existing_loggers=False,
    )

    root = logging.getLogger()
    level = _resolve_log_level()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    _apply_console_formatter(root)

    if _persistence_enabled():
        db_path = _resolve_persist_db_path(
            persist_db_path=persist_db_path,
            config_dir=resolved_config_dir,
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        persistent_handler = PersistentLogHandler(db_path=db_path)
        persistent_handler.setLevel(level)
        persistent_handler.setFormatter(JsonFormatter())
        root.addHandler(persistent_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def close_model_stream() -> None:
    return


def log_model_output(role_id: str, message: str) -> None:
    logger = get_logger(__name__)
    log_event(
        logger,
        logging.INFO,
        event="model.output",
        message="Model output emitted",
        payload={"role_id": role_id, "output": _safe_json(message)},
    )


def log_tool_call(role_id: str, tool_name: str, params: JsonObject) -> None:
    logger = get_logger(__name__)
    short = _safe_json(params)
    log_event(
        logger,
        logging.INFO,
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
    payload: JsonObject | None = None,
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


def _persistence_enabled() -> bool:
    raw = os.getenv("AGENT_TEAMS_LOG_PERSIST", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_log_level() -> int:
    level_name = os.getenv("AGENT_TEAMS_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    resolved = getattr(logging, level_name, logging.INFO)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _apply_console_formatter(root: logging.Logger) -> None:
    format_name = (
        os.getenv("AGENT_TEAMS_LOG_FORMAT", DEFAULT_LOG_FORMAT).strip().lower()
    )
    formatter: logging.Formatter
    if format_name == "console":
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
        )
    else:
        formatter = JsonFormatter()

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setFormatter(formatter)


def _resolve_persist_db_path(*, persist_db_path: Path | None, config_dir: Path) -> Path:
    if persist_db_path is not None:
        return persist_db_path.resolve()
    raw_path = os.getenv("AGENT_TEAMS_LOG_DB_PATH", "agent_teams.db")
    return _resolve_path(raw_path=raw_path, config_dir=config_dir)


def _ensure_logger_ini_exists(config_path: Path) -> None:
    if config_path.exists():
        return
    _ = config_path.write_text(DEFAULT_LOGGER_INI, encoding="utf-8")


def _file_config_defaults(config_dir: Path) -> dict[str, str]:
    log_path = (config_dir / "logs" / DEFAULT_LOG_FILENAME).resolve()
    return {"log_file": log_path.as_posix()}


def _ensure_file_handler_directories(*, config_path: Path, config_dir: Path) -> None:
    parser = ConfigParser(defaults=_file_config_defaults(config_dir))
    _ = parser.read(config_path, encoding="utf-8")

    sections = cast(list[str], parser.sections())
    for section in sections:
        if not section.startswith("handler_"):
            continue
        handler_class = parser.get(section, "class", fallback="")
        if "FileHandler" not in handler_class:
            continue

        handler_args = parser.get(section, "args", fallback="")
        file_path = _extract_file_path_from_args(handler_args)
        if file_path is None:
            continue
        resolved_file_path = _resolve_path(raw_path=file_path, config_dir=config_dir)
        resolved_file_path.parent.mkdir(parents=True, exist_ok=True)


def _extract_file_path_from_args(raw_args: str) -> str | None:
    try:
        parsed_args = cast(object, ast.literal_eval(raw_args))
    except (SyntaxError, ValueError):
        return None

    if isinstance(parsed_args, tuple) and parsed_args:
        tuple_args = cast(tuple[object, ...], parsed_args)
        first = tuple_args[0]
        if isinstance(first, str):
            return first
    if isinstance(parsed_args, list) and parsed_args:
        list_args = cast(list[object], parsed_args)
        first = list_args[0]
        if isinstance(first, str):
            return first
    return None


def _resolve_path(*, raw_path: str, config_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    return candidate.resolve()


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
