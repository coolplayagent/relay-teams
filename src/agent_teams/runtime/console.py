from __future__ import annotations

import json

_DEBUG_ENABLED = False
_OPEN_MODEL_STREAM_ROLE_ID: str | None = None

ROLE_LABELS = {
    'coordinator_agent': 'Coordinator Agent',
    'spec_builder': 'Spec Builder',
    'design_builder': 'Design Builder',
    'coder': 'Coder',
    'verify': 'Verify',
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


def log_model_output(role_id: str, message: str) -> None:
    close_model_stream()
    print(f'[{role_label(role_id)}] {message}')


def log_tool_call(role_id: str, tool_name: str, params: dict[str, object]) -> None:
    close_model_stream()
    short = _safe_json(params)
    print(f'[{role_label(role_id)}] tool call [{tool_name} {short}]')


def log_tool_error(role_id: str, payload: str) -> None:
    close_model_stream()
    print(f'[{role_label(role_id)}] tool error {payload}')


def log_model_stream_chunk(role_id: str, text: str) -> None:
    global _OPEN_MODEL_STREAM_ROLE_ID
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
