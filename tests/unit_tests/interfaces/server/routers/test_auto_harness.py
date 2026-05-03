# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock


from relay_teams.interfaces.server.routers.auto_harness import (
    GeneratedToolDetail,
    GeneratedToolSummary,
    _record_to_detail,
    _record_to_summary,
)
from relay_teams.tools.generated_tools import GeneratedToolStatus


def _fake_record() -> MagicMock:
    record = MagicMock()
    record.tool_name = "test_tool"
    record.description = "A test tool"
    record.status = GeneratedToolStatus.ENABLED
    record.target_role_id = "role-1"
    record.created_by_role_id = "role-2"
    record.version = 3
    record.test_cases = []
    record.input_schema = {"type": "object"}
    return record


def test_record_to_summary() -> None:
    record = _fake_record()
    result = _record_to_summary(record)
    assert isinstance(result, GeneratedToolSummary)
    assert result.tool_name == "test_tool"
    assert result.version == 3
    assert result.test_count == 0


def test_record_to_detail() -> None:
    record = _fake_record()
    result = _record_to_detail(record)
    assert isinstance(result, GeneratedToolDetail)
    assert result.tool_name == "test_tool"
    assert result.input_schema == {"type": "object"}
