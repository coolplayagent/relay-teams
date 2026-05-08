# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from enum import Enum

import pytest
from pydantic import BaseModel

from relay_teams.tools.runtime import argument_binding


class _Mode(Enum):
    FAST = "fast"
    SLOW = "slow"


class _Payload(BaseModel):
    value: int


def test_capture_tool_input_filters_private_excluded_and_unknown_args() -> None:
    def action(name: str, count: int) -> None:
        _ = (name, count)

    captured = argument_binding._capture_tool_input(
        raw_args={
            "ctx": object(),
            "_internal": "hidden",
            "name": "demo",
            "count": 2,
            "extra": "ignored",
        },
        action=action,
        exclude=("ctx",),
    )

    assert captured == {"name": "demo", "count": 2}


def test_capture_tool_input_dict_action_accepts_all_public_args() -> None:
    def action(tool_input: dict[str, object]) -> None:
        _ = tool_input

    captured = argument_binding._capture_tool_input(
        raw_args={"ctx": object(), "name": "demo", "enabled": True},
        action=action,
        exclude=("ctx",),
    )

    assert captured == {"name": "demo", "enabled": True}


def test_tool_input_parameter_names_handles_non_callable_and_var_kwargs() -> None:
    def action(name: str, *args: object, **kwargs: object) -> None:
        _ = (name, args, kwargs)

    assert argument_binding._tool_input_parameter_names(object()) is None
    assert argument_binding._tool_input_parameter_names(action) == {"name"}


def test_bind_tool_action_kwargs_coerces_common_types() -> None:
    def action(
        payload: _Payload,
        payloads: list[_Payload],
        mode: _Mode,
        enabled: bool,
        count: int,
        ratio: float,
        names: tuple,
        items: list,
        **kwargs: object,
    ) -> None:
        _ = (payload, payloads, mode, enabled, count, ratio, names, items, kwargs)

    parameters = list(inspect.signature(action).parameters.values())
    kwargs = argument_binding._bind_tool_action_kwargs(
        parameters=parameters,
        tool_input={
            "payload": {"value": 1},
            "payloads": [{"value": 2}],
            "mode": "fast",
            "enabled": "yes",
            "count": "3",
            "ratio": "4.5",
            "names": ["a", "b"],
            "items": ["x", "y"],
            "extra": "kept",
        },
        resolved_annotations={
            "payload": _Payload,
            "payloads": list[_Payload],
            "mode": _Mode,
            "enabled": bool,
            "count": int,
            "ratio": float,
            "names": tuple,
            "items": list,
        },
    )

    assert kwargs["payload"] == _Payload(value=1)
    assert kwargs["payloads"] == [_Payload(value=2)]
    assert kwargs["mode"] is _Mode.FAST
    assert kwargs["enabled"] is True
    assert kwargs["count"] == 3
    assert kwargs["ratio"] == 4.5
    assert kwargs["names"] == ("a", "b")
    assert kwargs["items"] == ["x", "y"]
    assert kwargs["extra"] == "kept"


def test_bind_tool_action_kwargs_uses_defaults_for_type_coercion() -> None:
    def action(
        enabled: bool = False,
        count: int = 0,
        ratio: float = 0.0,
        mode: _Mode = _Mode.SLOW,
    ) -> None:
        _ = (enabled, count, ratio, mode)

    kwargs = argument_binding._bind_tool_action_kwargs(
        parameters=list(inspect.signature(action).parameters.values()),
        tool_input={
            "enabled": 1,
            "count": 2.8,
            "ratio": True,
            "mode": "fast",
        },
    )

    assert kwargs == {
        "enabled": True,
        "count": 2,
        "ratio": 1.0,
        "mode": _Mode.FAST,
    }


def test_bind_tool_action_kwargs_resolves_optional_model_and_enum_annotations() -> None:
    def action(payload: _Payload | None, mode: _Mode | None) -> None:
        _ = (payload, mode)

    kwargs = argument_binding._bind_tool_action_kwargs(
        parameters=list(inspect.signature(action).parameters.values()),
        tool_input={"payload": {"value": 5}, "mode": "slow"},
        resolved_annotations=argument_binding._resolve_tool_action_annotations(action),
    )

    assert kwargs["payload"] == _Payload(value=5)
    assert kwargs["mode"] is _Mode.SLOW


def test_resolve_tool_action_annotations_returns_empty_for_unresolvable_hints() -> None:
    def action(name: object) -> None:
        _ = name

    action.__annotations__["name"] = "MissingType"

    assert argument_binding._resolve_tool_action_annotations(object()) == {}
    assert argument_binding._resolve_tool_action_annotations(action) == {}


def test_bind_tool_action_rejects_unsupported_positional_only() -> None:
    def action(name: str, /) -> None:
        _ = name

    with pytest.raises(TypeError, match="Unsupported tool action parameter kind"):
        argument_binding._bind_tool_action_kwargs(
            parameters=list(inspect.signature(action).parameters.values()),
            tool_input={"name": "demo"},
        )


def test_coercion_rejects_blank_numbers() -> None:
    parameter = inspect.Parameter(
        "count",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=int,
    )

    with pytest.raises(ValueError, match="Cannot coerce tool argument to int"):
        argument_binding._coerce_tool_argument_for_parameter(
            value=" ",
            parameter=parameter,
            annotation=int,
        )


def test_bool_and_float_coercion_cover_falsey_and_invalid_inputs() -> None:
    bool_parameter = inspect.Parameter(
        "enabled",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=bool,
    )
    float_parameter = inspect.Parameter(
        "ratio",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=float,
    )

    assert (
        argument_binding._coerce_tool_argument_for_parameter(
            value="off",
            parameter=bool_parameter,
            annotation=bool,
        )
        is False
    )
    assert (
        argument_binding._coerce_tool_argument_for_parameter(
            value=[],
            parameter=bool_parameter,
            annotation=bool,
        )
        is False
    )
    with pytest.raises(ValueError, match="Cannot coerce tool argument to float"):
        argument_binding._coerce_tool_argument_for_parameter(
            value=" ",
            parameter=float_parameter,
            annotation=float,
        )
