from __future__ import annotations

import pytest

from relay_teams.hooks.hook_models import (
    HookHandlerConfig,
    HookHandlerType,
    HookMatcherGroup,
    HookOnError,
)


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "type": "http",
                "url": "https://example.test/hook",
            },
            HookHandlerType.HTTP,
        ),
        (
            {
                "type": "prompt",
                "prompt": "review the output",
            },
            HookHandlerType.PROMPT,
        ),
        (
            {
                "type": "agent",
                "role_id": "Reviewer",
                "prompt": "review the final answer",
            },
            HookHandlerType.AGENT,
        ),
    ],
)
def test_hook_handler_config_accepts_type_specific_required_fields(
    payload: dict[str, str],
    expected_type: HookHandlerType,
) -> None:
    handler = HookHandlerConfig.model_validate(payload)

    assert handler.type == expected_type
    assert handler.timeout_seconds == 5.0
    assert handler.run_async is False
    assert handler.on_error == HookOnError.IGNORE


def test_hook_handler_config_accepts_alias_fields() -> None:
    handler = HookHandlerConfig.model_validate(
        {
            "type": "command",
            "command": "echo ok",
            "if": "Bash(git *)",
            "timeout": 12,
            "async": True,
        }
    )

    assert handler.if_rule == "Bash(git *)"
    assert handler.timeout_seconds == 12
    assert handler.run_async is True


@pytest.mark.parametrize(
    ("payload", "error_message"),
    [
        (
            {
                "type": "http",
            },
            "http hook requires url",
        ),
        (
            {
                "type": "prompt",
            },
            "prompt hook requires prompt",
        ),
        (
            {
                "type": "agent",
                "role_id": "Reviewer",
            },
            "agent hook requires prompt",
        ),
    ],
)
def test_hook_handler_config_rejects_missing_type_specific_required_fields(
    payload: dict[str, str],
    error_message: str,
) -> None:
    with pytest.raises(ValueError, match=error_message):
        HookHandlerConfig.model_validate(payload)


def test_hook_handler_config_allows_agent_without_role_id() -> None:
    handler = HookHandlerConfig.model_validate(
        {
            "type": "agent",
            "prompt": "review output",
        }
    )

    assert handler.role_id is None
    assert handler.prompt == "review output"


def test_hook_matcher_group_allows_empty_handlers_for_tolerant_runtime_parsing() -> (
    None
):
    group = HookMatcherGroup.model_validate({"matcher": "*", "hooks": []})

    assert group.hooks == ()
