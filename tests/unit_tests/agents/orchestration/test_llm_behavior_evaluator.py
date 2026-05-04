# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

from relay_teams.agents.orchestration.llm_behavior_evaluator import (
    LLMBehaviorEvaluator,
)
from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.providers.provider_contracts import LLMProvider


def test_evaluate_behavior_empty_constraints():
    provider = MagicMock(spec=LLMProvider)
    evaluator = LLMBehaviorEvaluator(provider=provider)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "shell", "args": {}},),
        result="done",
        constraints=(),
    )
    assert result == ()


def test_evaluate_behavior_empty_tool_calls():
    provider = MagicMock(spec=LLMProvider)
    evaluator = LLMBehaviorEvaluator(provider=provider)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=(),
        result="done",
        constraints=("no-network-calls",),
    )
    assert result == ()


def test_evaluate_behavior_fallback_on_provider_failure():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.side_effect = RuntimeError("provider down")
    evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "shell", "args": {"command": "curl example.com"}},),
        result="done",
        constraints=("no-network-calls",),
    )
    assert len(result) == 1
    assert result[0].layer == VerificationLayer.BEHAVIOR
    assert result[0].passed is True
    assert "fallback" in result[0].details.lower()


def test_evaluate_behavior_parse_violation():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = '{"violations": ["no-network-calls violated"]}'
    evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "shell", "args": {"command": "curl example.com"}},),
        result="done",
        constraints=("no-network-calls",),
    )
    assert len(result) == 1
    assert result[0].passed is False
    assert "no-network-calls" in result[0].name


def test_evaluate_behavior_parse_no_violations():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = '{"violations": []}'
    evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "read", "args": {}},),
        result="done",
        constraints=("no-network-calls",),
    )
    assert len(result) == 1
    assert result[0].passed is True


def test_evaluate_behavior_parse_malformed_json():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = "not valid json"
    evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "shell", "args": {}},),
        result="done",
        constraints=("no-network-calls",),
    )
    assert len(result) == 1
    assert result[0].passed is True
    assert "fallback" in result[0].details.lower()


def test_evaluate_behavior_parse_code_block_json():
    provider = MagicMock(spec=LLMProvider)
    provider.generate.return_value = '```json\n{"violations": []}\n```'
    evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
    result = evaluator.evaluate_behavior(
        task_id="task-1",
        tool_calls=({"tool_name": "read", "args": {}},),
        result="done",
        constraints=("no-network-calls",),
    )
    assert len(result) == 1
    assert result[0].passed is True


class TestLLMBehaviorEvaluatorCoverage:
    """Cover _build_prompt branch with tool calls."""

    def test_build_prompt_with_tool_calls(self) -> None:
        from relay_teams.agents.orchestration.llm_behavior_evaluator import (
            LLMBehaviorEvaluator,
        )

        prompt = LLMBehaviorEvaluator._build_prompt(
            task_id="t-1",
            tool_calls=({"tool_name": "edit"},),
            result="some result",
            constraints=("AC1: must work",),
        )
        assert "edit" in prompt
        assert "AC1" in prompt
