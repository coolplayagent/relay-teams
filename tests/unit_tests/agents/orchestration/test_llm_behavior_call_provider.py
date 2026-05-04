# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from relay_teams.agents.orchestration.llm_behavior_evaluator import LLMBehaviorEvaluator


class TestCallProviderExceptionPath:
    """Cover _call_provider exception branches."""

    def test_call_provider_returns_none_on_failure(self) -> None:
        provider = MagicMock()
        provider.generate = AsyncMock(side_effect=RuntimeError("boom"))
        evaluator = LLMBehaviorEvaluator(provider=provider, max_retries=0)
        result = evaluator._call_provider("test prompt")
        assert result is None
