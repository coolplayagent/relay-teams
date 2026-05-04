# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.agents.orchestration.verification import (
    VerificationEvaluatorFactory,
)


class _FakeProvider:
    """Minimal fake provider for factory tests."""


class TestVerificationEvaluatorFactory:
    def test_no_provider_no_base_returns_none(self) -> None:
        factory = VerificationEvaluatorFactory()
        assert factory.build() is None

    def test_base_evaluator_overrides(self) -> None:
        calls: list[int] = []

        def base_eval(request: object) -> object:
            calls.append(1)
            return object()

        factory = VerificationEvaluatorFactory(base_evaluator=base_eval)  # type: ignore[arg-type]
        result = factory.build()
        assert result is not None
        assert result is base_eval

    def test_with_provider_returns_evaluator(self) -> None:
        factory = VerificationEvaluatorFactory(llm_provider=_FakeProvider())  # type: ignore[arg-type]
        evaluator = factory.build()
        assert evaluator is not None
        assert callable(evaluator)
