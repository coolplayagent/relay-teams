# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import get_args, get_origin, get_type_hints

from pydantic import BaseModel, JsonValue

from relay_teams.tools.runtime.json_helpers import (
    normalize_json_value as _normalize_json_value,
)


def _capture_tool_input(
    *,
    raw_args: Mapping[str, object],
    action: Callable[..., object | Awaitable[object]] | object,
    exclude: tuple[str, ...],
) -> dict[str, JsonValue]:
    excluded = set(exclude)
    parameter_names = _tool_input_parameter_names(action)
    result: dict[str, JsonValue] = {}
    for name, value in raw_args.items():
        if name in excluded or name.startswith("_"):
            continue
        if parameter_names is not None and name not in parameter_names:
            continue
        result[name] = _normalize_json_value(value)
    return result


def _tool_input_parameter_names(
    action: Callable[..., object | Awaitable[object]] | object,
) -> set[str] | None:
    if not callable(action):
        return None
    parameters = list(inspect.signature(action).parameters.values())
    if not parameters or _uses_tool_input_dict(parameters):
        return None
    names: set[str] = set()
    for parameter in parameters:
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            names.add(parameter.name)
    return names


def _uses_tool_input_dict(parameters: list[inspect.Parameter]) -> bool:
    if len(parameters) != 1:
        return False
    parameter = parameters[0]
    return parameter.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    } and parameter.name in {"tool_input", "args", "tool_args"}


def _bind_tool_action_kwargs(
    *,
    parameters: list[inspect.Parameter],
    tool_input: dict[str, JsonValue],
    resolved_annotations: Mapping[str, object] | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for parameter in parameters:
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            kwargs.update(
                {key: value for key, value in tool_input.items() if key not in kwargs}
            )
            continue
        if parameter.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            raise TypeError(
                f"Unsupported tool action parameter kind: {parameter.kind.value}"
            )
        if parameter.name not in tool_input:
            continue
        kwargs[parameter.name] = _coerce_tool_argument_for_parameter(
            value=tool_input[parameter.name],
            parameter=parameter,
            annotation=_resolved_parameter_annotation(
                parameter=parameter,
                resolved_annotations=resolved_annotations,
            ),
        )
    return kwargs


def _coerce_tool_argument_for_parameter(
    *,
    value: JsonValue,
    parameter: inspect.Parameter,
    annotation: object,
) -> object:
    if value is None:
        return None
    model_list_type = _resolve_pydantic_model_list_type(annotation)
    if model_list_type is not None and isinstance(value, list):
        return [model_list_type.model_validate(item) for item in value]
    model_type = _resolve_pydantic_model_type(annotation)
    if model_type is not None and isinstance(value, dict):
        return model_type.model_validate(value)
    enum_type = _resolve_enum_type(annotation=annotation, parameter=parameter)
    if enum_type is not None and isinstance(value, str):
        return enum_type(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=bool
    ):
        return _coerce_bool(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=int
    ):
        return _coerce_int(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=float
    ):
        return _coerce_float(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=str
    ):
        return str(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=tuple
    ) and isinstance(value, list):
        return tuple(value)
    if _parameter_accepts_type(
        annotation=annotation, parameter=parameter, expected_type=list
    ) and isinstance(value, tuple):
        return list(value)
    return value


def _parameter_accepts_type(
    *,
    annotation: object,
    parameter: inspect.Parameter,
    expected_type: type[object],
) -> bool:
    if annotation is not inspect._empty and _annotation_contains_type(
        annotation=annotation,
        expected_type=expected_type,
    ):
        return True
    default = parameter.default
    if default is inspect._empty or default is None:
        return False
    return isinstance(default, expected_type)


def _annotation_contains_type(
    *,
    annotation: object,
    expected_type: type[object],
) -> bool:
    if annotation is expected_type:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(
        item is expected_type for item in get_args(annotation) if item is not type(None)
    )


def _resolve_pydantic_model_list_type(
    annotation: object,
) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin not in {list, tuple}:
        return None
    for item in get_args(annotation):
        if inspect.isclass(item) and issubclass(item, BaseModel):
            return item
    return None


def _resolve_pydantic_model_type(
    annotation: object,
) -> type[BaseModel] | None:
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is None:
        return None
    for item in get_args(annotation):
        if item is type(None):
            continue
        if inspect.isclass(item) and issubclass(item, BaseModel):
            return item
    return None


def _resolve_enum_type(
    *,
    annotation: object,
    parameter: inspect.Parameter,
) -> type[Enum] | None:
    if annotation is not inspect._empty:
        candidate = _enum_type_from_annotation(annotation)
        if candidate is not None:
            return candidate
    default = parameter.default
    if default is inspect._empty or not isinstance(default, Enum):
        return None
    return type(default)


def _enum_type_from_annotation(annotation: object) -> type[Enum] | None:
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return annotation
    origin = get_origin(annotation)
    if origin is None:
        return None
    for item in get_args(annotation):
        if item is type(None):
            continue
        if inspect.isclass(item) and issubclass(item, Enum):
            return item
    return None


def _resolve_tool_action_annotations(
    action: Callable[..., object | Awaitable[object]] | object,
) -> dict[str, object]:
    if not callable(action):
        return {}
    try:
        return get_type_hints(action)
    except (AttributeError, NameError, TypeError):
        return {}


def _resolved_parameter_annotation(
    *,
    parameter: inspect.Parameter,
    resolved_annotations: Mapping[str, object] | None,
) -> object:
    if resolved_annotations is None:
        return parameter.annotation
    return resolved_annotations.get(parameter.name, parameter.annotation)


def _coerce_bool(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_int(value: JsonValue) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return int(stripped)
    raise ValueError(f"Cannot coerce tool argument to int: {value!r}")


def _coerce_float(value: JsonValue) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return float(stripped)
    raise ValueError(f"Cannot coerce tool argument to float: {value!r}")
