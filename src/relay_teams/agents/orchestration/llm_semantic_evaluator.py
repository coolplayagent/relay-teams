# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import Callable

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRequestNode
from pydantic_ai.settings import ModelSettings

from relay_teams.agents.execution.model_builder import (
    RuntimeChatModel,
    build_base_model_settings,
    build_runtime_chat_model,
)
from relay_teams.agents.tasks.models import (
    SemanticEvaluationRequest,
    SemanticEvaluationResult,
)
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.model_config import ModelEndpointConfig

_EVALUATION_MAX_TOKENS = 1200
_EVALUATION_TEMPERATURE = 0.1


def _passed_label(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "N/A"


ModelConfigResolver = Callable[[], tuple[ModelEndpointConfig | None, str | None]]


class _LlmEvaluationOutput(BaseModel):
    model_config = {"extra": "forbid"}

    verdict: str
    confidence: float
    reason: str
    evidence_ids: tuple[str, ...] = ()


class LlmSemanticEvaluator:
    """Evaluates semantic verification criteria using an LLM.

    This evaluator makes a streaming LLM call to determine whether an
    acceptance criterion is satisfied by the task result and linked evidence.
    It uses low temperature and bounded token output for deterministic
    evaluation.

    The ``__call__`` method is synchronous because the verification pipeline
    runs synchronously.  Internally it bridges to the async LLM call via
    ``concurrent.futures`` or ``asyncio.run`` depending on the current
    event-loop context.
    """

    def __init__(
        self,
        *,
        resolve_model_config: ModelConfigResolver,
    ) -> None:
        self._resolve_model_config = resolve_model_config

    def __call__(self, request: SemanticEvaluationRequest) -> SemanticEvaluationResult:
        config, _profile_name = self._resolve_model_config()
        if config is None:
            raise RuntimeError(
                "LlmSemanticEvaluator could not resolve model configuration"
            )
        prompt = _build_semantic_evaluation_prompt(request)
        cache_scope = f"semantic_eval:{request.task_id}"
        model = _build_evaluator_model(config, cache_scope=cache_scope)
        settings = _evaluator_model_settings(config)
        agent: Agent[None, _LlmEvaluationOutput] = Agent(
            model=model,
            output_type=_LlmEvaluationOutput,
            instructions=_EVALUATOR_SYSTEM_INSTRUCTION,
            model_settings=settings,
            retries=1,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    _run_evaluator_streaming(agent=agent, prompt=prompt),
                )
                output = future.result()
        else:
            output = asyncio.run(_run_evaluator_streaming(agent=agent, prompt=prompt))
        return _to_semantic_result(output, request.criterion)


_EVALUATOR_SYSTEM_INSTRUCTION = (
    "You are a verification evaluator. Given the acceptance criterion, "
    "a result excerpt, and verification evidence, determine whether the "
    "criterion is satisfied. "
    'Return structured output with verdict "PASS", "FAIL", or "PARTIAL", '
    "a confidence score between 0.0 and 1.0, a reason for the verdict, "
    "and the evidence IDs that support your judgment."
)


def _build_semantic_evaluation_prompt(request: SemanticEvaluationRequest) -> str:
    parts: list[str] = [
        "## Semantic Verification Request",
        "",
        f"### Acceptance Criterion\n{request.criterion}",
        "",
    ]
    if request.result_excerpt:
        excerpt = request.result_excerpt
        if len(excerpt) > 2000:
            excerpt = excerpt[:1985] + " [truncated]"
        parts.append(f"### Result Excerpt\n{excerpt}")
        parts.append("")
    if request.evidence:
        parts.append("### Linked Evidence Items")
        for item in request.evidence:
            evidence_summary = item.summary
            evidence_excerpt = item.output_excerpt[:200] if item.output_excerpt else ""
            passed_label = _passed_label(item.passed)
            parts.append(f"- [{item.evidence_id}] ({passed_label}) {evidence_summary}")
            if evidence_excerpt:
                parts.append(f"  Output: {evidence_excerpt}")
        parts.append("")
    parts.append(
        "Evaluate whether the acceptance criterion is satisfied "
        "based on the result excerpt and linked evidence."
    )
    return "\n".join(parts)


async def _run_evaluator_streaming(
    *,
    agent: Agent[None, _LlmEvaluationOutput],
    prompt: str,
) -> _LlmEvaluationOutput:
    async with agent.iter(prompt) as agent_run:
        async for node in agent_run:
            if not isinstance(node, ModelRequestNode):
                continue
            async with node.stream(agent_run.ctx) as stream:
                async for _event in stream:
                    pass
        if agent_run.result is None:
            raise RuntimeError("LlmSemanticEvaluator did not produce a result")
        return agent_run.result.output


def _build_evaluator_model(
    config: ModelEndpointConfig,
    *,
    cache_scope: str | None,
) -> RuntimeChatModel:
    return build_runtime_chat_model(
        config=config,
        http_client=build_llm_http_client(
            connect_timeout_seconds=config.connect_timeout_seconds,
            ssl_verify=config.ssl_verify,
            cache_scope=cache_scope,
        ),
    )


def _evaluator_model_settings(config: ModelEndpointConfig) -> ModelSettings:
    capped_config = config.model_copy(
        update={
            "sampling": config.sampling.model_copy(
                update={
                    "temperature": min(
                        config.sampling.temperature, _EVALUATION_TEMPERATURE
                    ),
                    "max_tokens": min(
                        config.sampling.max_tokens or _EVALUATION_MAX_TOKENS,
                        _EVALUATION_MAX_TOKENS,
                    ),
                }
            )
        }
    )
    return build_base_model_settings(capped_config)


_VALID_PASS_VERDICTS = frozenset({"PASS", "pass", "Pass"})


def _to_semantic_result(
    output: _LlmEvaluationOutput, criterion: str
) -> SemanticEvaluationResult:
    verdict = output.verdict.strip()
    passed = verdict in _VALID_PASS_VERDICTS
    confidence = max(0.0, min(1.0, output.confidence))
    return SemanticEvaluationResult(
        criterion=criterion,
        passed=passed,
        confidence=confidence,
        reason=output.reason.strip(),
        evidence_ids=output.evidence_ids,
        evaluator="llm",
    )
