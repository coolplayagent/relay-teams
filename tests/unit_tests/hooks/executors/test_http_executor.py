from __future__ import annotations

import httpx
import pytest

from relay_teams.hooks.executors.http_executor import (
    NonBlockingHttpHookError,
    _interpolate_headers,
    _parse_http_response,
)
from relay_teams.hooks.hook_models import HookDecisionType, HookEventName


def test_http_executor_plain_text_response_adds_context() -> None:
    response = httpx.Response(
        200,
        text="review this notification",
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.NOTIFICATION,
    )

    assert decision.decision == HookDecisionType.OBSERVE
    assert decision.additional_context == ("review this notification",)


def test_http_executor_empty_response_uses_event_default() -> None:
    response = httpx.Response(
        200,
        text="  ",
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.NOTIFICATION,
    )

    assert decision.decision == HookDecisionType.OBSERVE
    assert decision.additional_context == ()


def test_http_executor_invalid_json_like_response_adds_context() -> None:
    response = httpx.Response(
        200,
        text="{not json}",
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.ALLOW
    assert decision.additional_context == ("{not json}",)


def test_http_executor_valid_json_response_uses_parser() -> None:
    response = httpx.Response(
        200,
        json={"decision": "deny", "reason": "policy"},
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.DENY
    assert decision.reason == "policy"


def test_http_executor_claude_block_retries_stop() -> None:
    response = httpx.Response(
        200,
        json={"continue": False, "stopReason": "needs tests"},
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.STOP,
    )

    assert decision.decision == HookDecisionType.RETRY
    assert decision.reason == "needs tests"


def test_http_executor_permission_request_discards_updated_input() -> None:
    response = httpx.Response(
        200,
        json={
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedInput": {"command": "ignored"},
                },
            }
        },
        request=httpx.Request("POST", "https://hook.test/"),
    )

    decision = _parse_http_response(
        response,
        event_name=HookEventName.PERMISSION_REQUEST,
    )

    assert decision.decision == HookDecisionType.ALLOW
    assert decision.updated_input is None


def test_http_executor_non_2xx_response_is_non_blocking_error() -> None:
    response = httpx.Response(
        500,
        text="failed",
        request=httpx.Request("POST", "https://hook.test/"),
    )

    with pytest.raises(NonBlockingHttpHookError, match="status 500"):
        _ = _parse_http_response(
            response,
            event_name=HookEventName.PRE_TOOL_USE,
        )


def test_http_executor_interpolates_only_allowed_header_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOOK_TOKEN", "secret-token")
    monkeypatch.setenv("OTHER_TOKEN", "hidden")

    headers = _interpolate_headers(
        headers={
            "Authorization": "Bearer ${HOOK_TOKEN}",
            "X-Other": "$OTHER_TOKEN",
        },
        allowed_env_vars=("HOOK_TOKEN",),
    )

    assert headers == {
        "Authorization": "Bearer secret-token",
        "X-Other": "",
    }


def test_http_executor_empty_headers_do_not_interpolate() -> None:
    assert _interpolate_headers(headers={}, allowed_env_vars=("HOOK_TOKEN",)) == {}
