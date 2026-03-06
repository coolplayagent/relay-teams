# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_teams.tools.runtime import ToolError, ToolResultEnvelope


def test_tool_result_envelope_defaults_meta_and_serializes_nested_error() -> None:
    error = ToolError(
        type="validation_error",
        message="bad input",
        retryable=True,
        suggested_fix="Adjust the path.",
    )

    envelope = ToolResultEnvelope(
        ok=False,
        tool="write",
        error=error,
    )

    payload = envelope.model_dump(mode="json")

    assert payload["meta"] == {}
    assert payload["error"] == {
        "type": "validation_error",
        "message": "bad input",
        "retryable": True,
        "suggested_fix": "Adjust the path.",
    }


def test_tool_result_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope.model_validate(
            {
                "ok": True,
                "tool": "read",
                "extra_field": "unexpected",
            }
        )
