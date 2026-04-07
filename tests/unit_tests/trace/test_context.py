# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

import pytest

from relay_teams.trace import (
    bind_trace_context,
    generate_request_id,
    generate_span_id,
    generate_trace_id,
    get_trace_context,
    trace_span,
)


def test_bind_trace_context_applies_and_restores_context() -> None:
    assert get_trace_context().trace_id is None

    with bind_trace_context(trace_id="trace-a", request_id="req-a"):
        context = get_trace_context()
        assert context.trace_id == "trace-a"
        assert context.request_id == "req-a"

        with bind_trace_context(task_id="task-1"):
            nested = get_trace_context()
            assert nested.trace_id == "trace-a"
            assert nested.request_id == "req-a"
            assert nested.task_id == "task-1"

        restored = get_trace_context()
        assert restored.trace_id == "trace-a"
        assert restored.request_id == "req-a"
        assert restored.task_id is None

    final = get_trace_context()
    assert final.trace_id is None
    assert final.request_id is None
    assert final.task_id is None


def test_generate_ids_use_expected_prefix() -> None:
    assert generate_request_id().startswith("req_")
    assert generate_span_id().startswith("span_")
    assert generate_trace_id().startswith("trace_")


def test_bind_trace_context_supports_trigger_id() -> None:
    with bind_trace_context(trigger_id="trigger-1"):
        assert get_trace_context().trigger_id == "trigger-1"


def test_bind_trace_context_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Unknown trace context fields"):
        with bind_trace_context(skill_name="time"):
            pass


def test_trace_span_generates_nested_span_hierarchy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.unit.trace")

    with caplog.at_level(logging.DEBUG, logger="tests.unit.trace"):
        with trace_span(logger, component="trace.tests", operation="root"):
            root_context = get_trace_context()
            assert root_context.trace_id is not None
            assert root_context.span_id is not None
            assert root_context.parent_span_id is None

            with trace_span(logger, component="trace.tests", operation="child"):
                child_context = get_trace_context()
                assert child_context.trace_id == root_context.trace_id
                assert child_context.span_id is not None
                assert child_context.span_id != root_context.span_id
                assert child_context.parent_span_id == root_context.span_id

    assert get_trace_context().trace_id is None
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events == [
        "trace.span.succeeded",
        "trace.span.succeeded",
    ]
