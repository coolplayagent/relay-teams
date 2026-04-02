# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from agent_teams.providers.model_config import ModelRequestHeader

_MODEL_HEADER_SECRET_FIELD_PREFIX = "header:"


def model_header_secret_field_name(name: str) -> str:
    return f"{_MODEL_HEADER_SECRET_FIELD_PREFIX}{name.strip().casefold()}"


def normalize_model_request_headers_payload(
    raw_value: object,
) -> tuple[ModelRequestHeader, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise ValueError("headers must be a list")
    bindings: list[ModelRequestHeader] = []
    for item in raw_value:
        if not isinstance(item, dict):
            raise ValueError("headers items must be objects")
        payload: dict[str, JsonValue] = {
            "name": item.get("name"),
            "value": item.get("value"),
            "secret": item.get("secret", False),
            "configured": item.get("configured", False),
        }
        bindings.append(ModelRequestHeader.model_validate(payload))
    return tuple(bindings)
