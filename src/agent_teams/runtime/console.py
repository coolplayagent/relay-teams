from __future__ import annotations

import json
import logging

from agent_teams.runtime.logging import get_logger, log_event

_DEBUG_ENABLED = False
_OPEN_MODEL_STREAM_ROLE_ID: str | None = None
logger = get_logger(__name__)

ROLE_LABELS = {
    'coordinator_agent': 'Coordinator Agent',
    'spec_spec': 'Spec Spec',
    'spec_design': 'Spec Design',
    'spec_coder': 'Spec Coder',
    'spec_verify': 'Spec Verify',
}


def set_debug(enabled: bool) -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled


def is_debug() -> bool:
    return _DEBUG_ENABLED


def role_label(role_id: str) -> str:
    if role_id in ROLE_LABELS:
        return ROLE_LABELS[role_id]
    return role_id.replace('_', ' ').title()


def log_debug(message: str) -> None:
    if _DEBUG_ENABLED:
        close_model_stream()
        print(message)
    log_event(logger, logging.DEBUG, event='runtime.debug', message=message)


def log_model_output(role_id: str, message: str) -> None:
    close_model_stream()
    if _DEBUG_ENABLED:
        print(f'[{role_label(role_id)}] {message}')
    log_event(
        logger,
        logging.INFO,
        event='model.output',
        message='Model output emitted',
        payload={'role_id': role_id, 'output': _safe_json(message)},
    )


def log_tool_call(role_id: str, tool_name: str, params: dict[str, object]) -> None:
    close_model_stream()
    short = _safe_json(params)
    if _DEBUG_ENABLED:
        print(f'[{role_label(role_id)}] tool call [{tool_name} {short}]')
    log_event(
        logger,
        logging.INFO,
        event='tool.call.started',
        message='Tool call started',
        payload={'role_id': role_id, 'tool_name': tool_name, 'params': short},
    )


def log_tool_error(role_id: str, payload: str) -> None:
    close_model_stream()
    if _DEBUG_ENABLED:
        print(f'[{role_label(role_id)}] tool error {payload}')
    log_event(
        logger,
        logging.ERROR,
        event='tool.call.failed',
        message='Tool call failed',
        payload={'role_id': role_id, 'detail': payload},
    )


def log_model_stream_chunk(role_id: str, text: str) -> None:
    global _OPEN_MODEL_STREAM_ROLE_ID
    if _DEBUG_ENABLED:
        if _OPEN_MODEL_STREAM_ROLE_ID != role_id:
            close_model_stream()
            print(f'[{role_label(role_id)}] ', end='', flush=True)
            _OPEN_MODEL_STREAM_ROLE_ID = role_id
        print(text, end='', flush=True)


def close_model_stream() -> None:
    global _OPEN_MODEL_STREAM_ROLE_ID
    if _OPEN_MODEL_STREAM_ROLE_ID is not None:
        print()
        _OPEN_MODEL_STREAM_ROLE_ID = None


def _safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 300:
        return text[:300] + '...(truncated)'
    return text
