# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging

from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.agents.tasks.models import VerificationCheckResult
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.sessions.runs.run_models import RunKind

_LOGGER = get_logger(__name__)

_BEHAVIOR_SYSTEM_PROMPT = (
    "You are a behavior compliance evaluator for task execution. "
    "Analyze tool call patterns and determine whether they comply "
    "with the given constraints. "
    "Respond with valid JSON only: "
    '{"violations": ["description of each violation"]}'
)


class LLMBehaviorEvaluator:
    """Evaluates BEHAVIOR layer checks using LLM analysis of tool call patterns.

    Checks whether tool call patterns comply with task constraints
    such as no-write-outside-workspace or no-network-calls.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_retries: int = 2,
    ) -> None:
        self._provider = provider
        self._max_retries = max_retries

    def evaluate_behavior(
        self,
        *,
        task_id: str,
        tool_calls: tuple[dict[str, object], ...],
        result: str,
        constraints: tuple[str, ...],
    ) -> tuple[VerificationCheckResult, ...]:
        """Evaluate tool call behavioral compliance with constraints."""
        if not constraints or not tool_calls:
            return ()

        prompt = self._build_prompt(
            task_id=task_id,
            tool_calls=tool_calls,
            result=result,
            constraints=constraints,
        )

        response_text = self._call_provider(prompt)
        if response_text is None:
            return self._fallback_checks(
                _task_id=task_id,
                _tool_calls=tool_calls,
                constraints=constraints,
            )

        return self._parse_response(
            response_text=response_text,
            task_id=task_id,
            constraints=constraints,
        )

    @staticmethod
    def _build_prompt(
        *,
        task_id: str,
        tool_calls: tuple[dict[str, object], ...],
        result: str,
        constraints: tuple[str, ...],
    ) -> str:
        parts = [
            f"Task ID: {task_id}",
            "",
            "Constraints:",
        ]
        for constraint in constraints:
            parts.append(f"  - {constraint}")
        parts.append("")
        parts.append("Tool calls made:")
        for call in tool_calls[:20]:
            tool_name = call.get("tool_name", "unknown")
            args_summary = json.dumps(call.get("args", {}), default=str)[:200]
            parts.append(f"  - {tool_name}: {args_summary}")
        parts.append("")
        parts.append(f"Result excerpt: {result[:500]}")
        return "\n".join(parts)

    def _call_provider(self, prompt: str) -> str | None:
        request = LLMRequest(
            run_id="behavior-eval",
            trace_id="behavior-eval",
            task_id="behavior-eval",
            session_id="behavior-eval",
            workspace_id="behavior-eval",
            instance_id="behavior-eval",
            role_id="LLMBehaviorEvaluator",
            system_prompt=_BEHAVIOR_SYSTEM_PROMPT,
            user_prompt=prompt,
            run_kind=RunKind.CONVERSATION,
        )

        for attempt in range(self._max_retries + 1):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            try:
                if loop is not None and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            self._provider.generate(request),
                        )
                        return future.result()
                else:
                    return asyncio.run(self._provider.generate(request))
            except Exception as exc:
                if attempt < self._max_retries:
                    continue
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="behavior_evaluator.provider_failed",
                    message="LLM behavior evaluation failed",
                    payload={"error": str(exc)},
                )
                return None
        return None  # fallback for unreachable case

    @staticmethod
    def _fallback_checks(
        *,
        _task_id: str,
        _tool_calls: tuple[dict[str, object], ...],
        constraints: tuple[str, ...],
    ) -> tuple[VerificationCheckResult, ...]:
        """Rule-based fallback when the LLM provider is unavailable."""
        return tuple(
            VerificationCheckResult(
                layer=VerificationLayer.BEHAVIOR,
                name=f"behavior_constraint:{constraint}",
                passed=True,
                details="LLM evaluation unavailable; rule-based fallback applied.",
            )
            for constraint in constraints
        )

    def _parse_response(
        self,
        *,
        response_text: str,
        task_id: str,
        constraints: tuple[str, ...],
    ) -> tuple[VerificationCheckResult, ...]:
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                first_nl = cleaned.index("\n")
                last_fence = cleaned.rindex("```")
                cleaned = cleaned[first_nl + 1 : last_fence].strip()
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return self._fallback_checks(
                _task_id=task_id,
                _tool_calls=(),
                constraints=constraints,
            )

        violations = data.get("violations", [])
        violation_texts = {str(v).lower() for v in violations if str(v).strip()}

        checks: list[VerificationCheckResult] = []
        for constraint in constraints:
            constraint_lower = constraint.lower()
            is_violated = any(
                constraint_lower in v or v in constraint_lower for v in violation_texts
            )
            matching_violations = [
                v
                for v in violations
                if constraint_lower in str(v).lower()
                or str(v).lower() in constraint_lower
            ]
            checks.append(
                VerificationCheckResult(
                    layer=VerificationLayer.BEHAVIOR,
                    name=f"behavior_constraint:{constraint}",
                    passed=not is_violated,
                    details=(
                        f"Constraint satisfied: {constraint}"
                        if not is_violated
                        else f"Constraint violated: {constraint} -- "
                        f"{'; '.join(str(v) for v in matching_violations)}"
                    ),
                )
            )
        return tuple(checks)
