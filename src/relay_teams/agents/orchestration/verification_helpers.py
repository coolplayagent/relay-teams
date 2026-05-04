# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import concurrent.futures

from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest


def run_verification_llm_call(
    *,
    provider: LLMProvider,
    criterion: str,
    excerpt: str,
) -> str:
    """Build a verification prompt and call the LLM provider synchronously.

    This is a thin wrapper so the verification pipeline stays synchronous
    while still delegating to the async provider infrastructure.
    """
    prompt = (
        "Evaluate whether the following task output satisfies the given criterion.\n"
        f"Criterion: {criterion}\n"
        f"Task output excerpt (first 2000 chars):\n"
        f"{excerpt}\n\n"
        'Respond with JSON: {{"passed": true/false, '
        '"confidence": 0.0-1.0, "reason": "..."}}'
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    _call_provider(provider, prompt),
                )
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(_call_provider(provider, prompt))
    except RuntimeError:
        return asyncio.run(_call_provider(provider, prompt))


async def _call_provider(provider: LLMProvider, prompt: str) -> str:
    request = LLMRequest(
        run_id="verification",
        trace_id="verification",
        task_id="verification",
        session_id="verification",
        workspace_id="verification",
        instance_id="verification",
        role_id="verification",
        system_prompt="You are a verification evaluator.",
        user_prompt=prompt,
    )
    result = await provider.generate(request)
    return result if isinstance(result, str) else str(result)
