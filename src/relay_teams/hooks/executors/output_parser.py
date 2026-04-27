from __future__ import annotations

from collections.abc import Mapping
from typing import Optional, TypeGuard

from pydantic import JsonValue

from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionType,
    HookEventName,
)


def parse_hook_decision_payload(
    payload: object,
    *,
    event_name: HookEventName,
) -> HookDecision:
    if not isinstance(payload, Mapping):
        return parse_empty_hook_output(event_name=event_name)
    raw_payload = dict(payload)
    if _is_relay_decision_payload(raw_payload):
        return HookDecision(
            decision=HookDecisionType(str(raw_payload["decision"])),
            reason=str(raw_payload.get("reason") or ""),
            updated_input=_json_value_or_none(raw_payload.get("updated_input")),
            additional_context=_context_values(raw_payload),
            set_env=_string_mapping(raw_payload.get("set_env")),
            deferred_action=str(raw_payload.get("deferred_action") or ""),
        )
    if raw_payload.get("continue") is False:
        return HookDecision(
            decision=_block_decision_type(event_name),
            reason=str(raw_payload.get("stopReason") or ""),
        )
    decision_value = str(raw_payload.get("decision") or "").strip()
    if decision_value == "block":
        return HookDecision(
            decision=_block_decision_type(event_name),
            reason=str(raw_payload.get("reason") or ""),
        )
    specific_output = raw_payload.get("hookSpecificOutput")
    if isinstance(specific_output, Mapping):
        decision = _parse_hook_specific_output(
            specific_output,
            event_name=event_name,
        )
        if decision is not None:
            return decision
    context_values = _context_values(raw_payload)
    if context_values:
        return HookDecision(
            decision=HookDecisionType.ADDITIONAL_CONTEXT,
            additional_context=context_values,
        )
    return parse_empty_hook_output(event_name=event_name)


def parse_empty_hook_output(*, event_name: HookEventName) -> HookDecision:
    if event_name in {
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }:
        return HookDecision(decision=HookDecisionType.CONTINUE)
    if event_name in {
        HookEventName.SESSION_END,
        HookEventName.STOP_FAILURE,
        HookEventName.SUBAGENT_START,
        HookEventName.PERMISSION_DENIED,
        HookEventName.NOTIFICATION,
        HookEventName.INSTRUCTIONS_LOADED,
        HookEventName.POST_COMPACT,
    }:
        return HookDecision(decision=HookDecisionType.OBSERVE)
    return HookDecision(decision=HookDecisionType.ALLOW)


def _is_relay_decision_payload(payload: dict[object, object]) -> bool:
    decision = payload.get("decision")
    if not isinstance(decision, str):
        return False
    return decision in {item.value for item in HookDecisionType}


def _parse_hook_specific_output(
    output: Mapping[object, object],
    *,
    event_name: HookEventName,
) -> Optional[HookDecision]:
    raw_event_name = str(output.get("hookEventName") or "").strip()
    if raw_event_name and raw_event_name != event_name.value:
        return None
    if event_name == HookEventName.PRE_TOOL_USE:
        raw_permission = str(output.get("permissionDecision") or "").strip()
        decision = _permission_decision(raw_permission)
        if decision is None:
            return None
        return HookDecision(
            decision=decision,
            reason=str(output.get("permissionDecisionReason") or ""),
            updated_input=_json_value_or_none(output.get("updatedInput")),
            additional_context=_context_values(dict(output)),
        )
    if event_name == HookEventName.PERMISSION_REQUEST:
        raw_decision = output.get("decision")
        if not isinstance(raw_decision, Mapping):
            return None
        behavior = str(raw_decision.get("behavior") or "").strip()
        decision = _permission_decision(behavior)
        if decision is None or decision not in {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
        }:
            return None
        return HookDecision(
            decision=decision,
            reason=str(raw_decision.get("reason") or output.get("reason") or ""),
        )
    context_values = _context_values(dict(output))
    if context_values:
        return HookDecision(
            decision=HookDecisionType.ADDITIONAL_CONTEXT,
            additional_context=context_values,
        )
    return None


def _permission_decision(value: str) -> Optional[HookDecisionType]:
    if value == "allow":
        return HookDecisionType.ALLOW
    if value == "deny":
        return HookDecisionType.DENY
    if value == "ask":
        return HookDecisionType.ASK
    if value == "defer":
        return HookDecisionType.DEFER
    return None


def _block_decision_type(event_name: HookEventName) -> HookDecisionType:
    if event_name in {HookEventName.STOP, HookEventName.SUBAGENT_STOP}:
        return HookDecisionType.RETRY
    return HookDecisionType.DENY


def _context_values(payload: dict[object, object]) -> tuple[str, ...]:
    contexts: list[str] = []
    for key in ("additionalContext", "additional_context", "systemMessage"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            contexts.append(value.strip())
        elif isinstance(value, list):
            contexts.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(contexts)


def _json_value_or_none(value: object) -> Optional[JsonValue]:
    if value is None:
        return None
    return value if _is_json_value(value) else None


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key] = item
    return result


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False
