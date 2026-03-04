from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_teams.runtime.log_persistence import PersistentLogHandler
from agent_teams.runtime.trace import get_trace_context

SERVICE_NAME = 'agent_teams'


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            'ts': datetime.now(UTC).isoformat(),
            'level': record.levelname,
            'service': SERVICE_NAME,
            'env': os.getenv('AGENT_TEAMS_ENV', 'dev'),
            'logger': record.name,
            'message': record.getMessage(),
        }

        context = get_trace_context()
        if context.trace_id:
            payload['trace_id'] = context.trace_id
        if context.request_id:
            payload['request_id'] = context.request_id
        if context.session_id:
            payload['session_id'] = context.session_id
        if context.run_id:
            payload['run_id'] = context.run_id
        if context.task_id:
            payload['task_id'] = context.task_id
        if context.instance_id:
            payload['instance_id'] = context.instance_id
        if context.role_id:
            payload['role_id'] = context.role_id
        if context.tool_call_id:
            payload['tool_call_id'] = context.tool_call_id

        event = getattr(record, 'event', None)
        if event:
            payload['event'] = event

        duration_ms = getattr(record, 'duration_ms', None)
        if duration_ms is not None:
            payload['duration_ms'] = duration_ms

        log_payload = getattr(record, 'payload', None)
        if log_payload is not None:
            payload['payload'] = sanitize_payload(log_payload)

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload['error'] = {
                'type': exc_type.__name__ if exc_type else 'Exception',
                'message': str(exc_value),
                'stack': ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            }

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, persist_db_path: Path | None = None) -> None:
    level_name = os.getenv('AGENT_TEAMS_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.getenv('AGENT_TEAMS_LOG_FORMAT', 'json').lower()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    stream_handler = logging.StreamHandler()
    if fmt == 'console':
        stream_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s'))
    else:
        stream_handler.setFormatter(JsonFormatter())
    root.addHandler(stream_handler)

    if _persistence_enabled():
        db_path = persist_db_path or Path(os.getenv('AGENT_TEAMS_LOG_DB_PATH', '.agent_teams/agent_teams.db'))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        persistent_handler = PersistentLogHandler(db_path=db_path)
        persistent_handler.setFormatter(JsonFormatter())
        root.addHandler(persistent_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    *,
    event: str,
    message: str,
    payload: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    exc_info: Any = None,
) -> None:
    logger.log(
        level,
        message,
        extra={
            'event': event,
            'payload': sanitize_payload(payload or {}),
            'duration_ms': duration_ms,
        },
        exc_info=exc_info,
    )


def sanitize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(k): sanitize_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [sanitize_payload(v) for v in payload]
    if isinstance(payload, tuple):
        return [sanitize_payload(v) for v in payload]
    if isinstance(payload, str):
        return _truncate(_mask_sensitive(payload))
    return payload


def _persistence_enabled() -> bool:
    raw = os.getenv('AGENT_TEAMS_LOG_PERSIST', '1').strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _mask_sensitive(value: str) -> str:
    lowered = value.lower()
    if 'sk-' in lowered or 'bearer ' in lowered:
        return '***'
    return value


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return f'{value[:limit]}...(truncated)'
