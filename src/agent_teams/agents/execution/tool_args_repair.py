# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import cast

from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.messages import INVALID_JSON_KEY


class ToolArgsRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_args: dict[str, JsonValue]
    arguments_json: str
    repair_applied: bool = False
    repair_succeeded: bool = False
    fallback_invalid_json: bool = False


def repair_tool_args(
    args: str | dict[str, object] | None,
) -> ToolArgsRepairResult:
    if isinstance(args, dict):
        normalized = _coerce_json_object(args)
        if normalized is not None:
            normalized_json = json.dumps(normalized, ensure_ascii=False)
            return ToolArgsRepairResult(
                normalized_args=normalized,
                arguments_json=normalized_json,
            )
        raw_args = json.dumps(args, ensure_ascii=False, default=str)
        return _invalid_json_result(raw_args, repair_applied=False)
    if args is None or args == "":
        return ToolArgsRepairResult(
            normalized_args={},
            arguments_json="{}",
        )

    strict_object = _parse_json_object(args)
    if strict_object is not None:
        return ToolArgsRepairResult(
            normalized_args=strict_object,
            arguments_json=args,
        )

    repaired_object = _repair_json_object(args)
    if repaired_object is not None:
        return ToolArgsRepairResult(
            normalized_args=repaired_object,
            arguments_json=json.dumps(repaired_object, ensure_ascii=False),
            repair_applied=True,
            repair_succeeded=True,
        )

    return _invalid_json_result(args, repair_applied=True)


def _invalid_json_result(
    raw_args: str,
    *,
    repair_applied: bool,
) -> ToolArgsRepairResult:
    fallback = {INVALID_JSON_KEY: cast(JsonValue, raw_args)}
    return ToolArgsRepairResult(
        normalized_args=fallback,
        arguments_json=json.dumps(fallback, ensure_ascii=False),
        repair_applied=repair_applied,
        repair_succeeded=False,
        fallback_invalid_json=True,
    )


def _repair_json_object(raw_args: str) -> dict[str, JsonValue] | None:
    try:
        repaired = repair_json(
            raw_args,
            return_objects=True,
            stream_stable=True,
        )
    except Exception:
        return None
    return _coerce_json_object(repaired)


def _parse_json_object(raw_args: str) -> dict[str, JsonValue] | None:
    try:
        parsed = json.loads(raw_args)
    except ValueError:
        return None
    return _coerce_json_object(parsed)


def _coerce_json_object(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, JsonValue] = {}
    entries = cast(dict[object, object], value)
    for key, item in entries.items():
        if not isinstance(key, str):
            return None
        normalized_value = _coerce_json_value(item)
        if normalized_value is None:
            return None
        normalized[key] = normalized_value
    return normalized


def _coerce_json_value(value: object) -> JsonValue | None:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return cast(JsonValue, value)
    if isinstance(value, list):
        items = cast(list[object], value)
        normalized_items: list[JsonValue] = []
        for item in items:
            normalized = _coerce_json_value(item)
            if normalized is None:
                return None
            normalized_items.append(normalized)
        return normalized_items
    if isinstance(value, dict):
        return _coerce_json_object(value)
    return None
