# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from relay_teams.media import ContentPartsAdapter, content_parts_to_text


def parse_terminal_payload_json(payload_json: object) -> dict[str, object]:
    if not isinstance(payload_json, str) or not payload_json:
        return {}
    try:
        decoded = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(key): value for key, value in decoded.items() if isinstance(key, str)}


def extract_terminal_output(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    output_value = payload.get("output")
    if isinstance(output_value, str):
        return output_value.strip()
    if isinstance(output_value, (list, tuple)):
        try:
            parts = ContentPartsAdapter.validate_python(tuple(output_value))
        except Exception:
            return ""
        return content_parts_to_text(parts)
    return ""


def extract_terminal_error(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    error_value = payload.get("error")
    if isinstance(error_value, str):
        return error_value.strip()
    return ""
