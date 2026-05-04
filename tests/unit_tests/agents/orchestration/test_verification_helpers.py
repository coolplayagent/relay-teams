# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock


from relay_teams.agents.orchestration.verification_helpers import (
    run_verification_llm_call,
)


class TestRunVerificationLlmCall:
    def test_returns_string_from_provider(self) -> None:
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value='{"passed": true}')
        result = run_verification_llm_call(
            provider=provider,
            criterion="Output must be non-empty",
            excerpt="Some output text",
        )
        assert result == '{"passed": true}'

    def test_handles_non_string_result(self) -> None:
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=42)
        result = run_verification_llm_call(
            provider=provider,
            criterion="Test",
            excerpt="Excerpt",
        )
        assert result == "42"

    def test_running_loop_uses_thread(self) -> None:
        """When called from within an async context (running loop),
        the function should use a thread pool to avoid deadlock."""
        import asyncio

        provider = AsyncMock()
        provider.generate = AsyncMock(return_value='{"passed": true}')

        async def _inner() -> None:
            result = run_verification_llm_call(
                provider=provider,
                criterion="Test",
                excerpt="Excerpt",
            )
            assert result == '{"passed": true}'

        asyncio.run(_inner())
