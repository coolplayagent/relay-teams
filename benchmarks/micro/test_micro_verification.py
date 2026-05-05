# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.agents.tasks.models import VerificationCheckResult


def _build_verification_checks(count: int) -> tuple[VerificationCheckResult, ...]:
    results: list[VerificationCheckResult] = []
    layers = list(VerificationLayer)
    for i in range(count):
        results.append(
            VerificationCheckResult(
                layer=layers[i % len(layers)],
                name=f"check-{i}",
                passed=i % 3 != 0,
                details=f"Details for check {i}",
            )
        )
    return tuple(results)


def _evaluate_checks(checks: tuple[VerificationCheckResult, ...]) -> bool:
    return all(c.passed for c in checks)


def test_micro_verification_build_100(benchmark):
    result = benchmark(_build_verification_checks, 100)
    assert len(result) == 100


def test_micro_verification_build_500(benchmark):
    result = benchmark(_build_verification_checks, 500)
    assert len(result) == 500


def test_micro_verification_evaluate_100(benchmark):
    checks = _build_verification_checks(100)
    _ = benchmark(_evaluate_checks, checks)


def test_micro_verification_evaluate_500(benchmark):
    checks = _build_verification_checks(500)
    _ = benchmark(_evaluate_checks, checks)


def test_micro_verification_serialization(benchmark):
    checks = _build_verification_checks(100)

    def _roundtrip() -> tuple[VerificationCheckResult, ...]:
        data = "[" + ",".join(c.model_dump_json() for c in checks) + "]"
        import json

        raw = json.loads(data)
        return tuple(VerificationCheckResult.model_validate(r) for r in raw)

    result = benchmark(_roundtrip)
    assert len(result) == 100
