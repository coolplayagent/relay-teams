from __future__ import annotations

from relay_teams.hooks.executors.output_parser import (
    parse_empty_hook_output,
    parse_hook_decision_payload,
)
from relay_teams.hooks.hook_models import HookDecisionType, HookEventName


def test_parse_hook_decision_payload_accepts_relay_decision_payload() -> None:
    decision = parse_hook_decision_payload(
        {
            "decision": "updated_input",
            "reason": "rewrite",
            "updated_input": {"command": "git status"},
            "additional_context": ["ctx"],
            "additionalContext": ["claude ctx"],
            "set_env": {"KEEP": "value", "DROP": 3, 4: "ignored"},
            "deferred_action": "follow up",
        },
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.UPDATED_INPUT
    assert decision.reason == "rewrite"
    assert decision.updated_input == {"command": "git status"}
    assert decision.additional_context == ("claude ctx", "ctx")
    assert decision.set_env == {"KEEP": "value"}
    assert decision.deferred_action == "follow up"


def test_parse_hook_decision_payload_ignores_invalid_relay_decision_fields() -> None:
    decision = parse_hook_decision_payload(
        {
            "decision": "updated_input",
            "updated_input": object(),
            "set_env": "ignored",
        },
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.UPDATED_INPUT
    assert decision.updated_input is None
    assert decision.set_env == {}


def test_parse_hook_decision_payload_handles_claude_block_forms() -> None:
    pre_tool = parse_hook_decision_payload(
        {"continue": False, "stopReason": "blocked"},
        event_name=HookEventName.PRE_TOOL_USE,
    )
    stop = parse_hook_decision_payload(
        {"decision": "block", "reason": "retry"},
        event_name=HookEventName.STOP,
    )

    assert pre_tool.decision == HookDecisionType.DENY
    assert pre_tool.reason == "blocked"
    assert stop.decision == HookDecisionType.RETRY
    assert stop.reason == "retry"


def test_parse_hook_decision_payload_handles_pre_tool_specific_output() -> None:
    decision = parse_hook_decision_payload(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "needs approval",
                "updatedInput": {"command": "git status"},
                "additionalContext": ["ctx", " "],
            }
        },
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.ASK
    assert decision.reason == "needs approval"
    assert decision.updated_input == {"command": "git status"}
    assert decision.additional_context == ("ctx",)


def test_parse_hook_decision_payload_ignores_mismatched_specific_output() -> None:
    decision = parse_hook_decision_payload(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny"},
            }
        },
        event_name=HookEventName.PRE_TOOL_USE,
    )

    assert decision.decision == HookDecisionType.ALLOW


def test_parse_hook_decision_payload_handles_permission_request_specific_output() -> (
    None
):
    decision = parse_hook_decision_payload(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": "too risky"},
            }
        },
        event_name=HookEventName.PERMISSION_REQUEST,
    )

    assert decision.decision == HookDecisionType.DENY
    assert decision.reason == "too risky"


def test_parse_hook_decision_payload_extracts_context_fallbacks() -> None:
    decision = parse_hook_decision_payload(
        {
            "additionalContext": "one",
            "additional_context": ["two", ""],
            "systemMessage": "three",
        },
        event_name=HookEventName.NOTIFICATION,
    )

    assert decision.decision == HookDecisionType.ADDITIONAL_CONTEXT
    assert decision.additional_context == ("one", "two", "three")


def test_parse_empty_hook_output_uses_event_defaults() -> None:
    assert (
        parse_empty_hook_output(event_name=HookEventName.POST_TOOL_USE).decision
        == HookDecisionType.CONTINUE
    )
    assert (
        parse_empty_hook_output(event_name=HookEventName.NOTIFICATION).decision
        == HookDecisionType.OBSERVE
    )
    assert (
        parse_empty_hook_output(event_name=HookEventName.USER_PROMPT_SUBMIT).decision
        == HookDecisionType.ALLOW
    )
