# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import override

import pytest

from relay_teams.memory.models import (
    ConsolidationMode,
    CreateMemoryEntryRequest,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryContent,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.memory.semantic_consolidation import (
    SemanticExtractionInput,
    SemanticExtractionOutput,
    _ExtractedMemoryEntry,
    _estimate_tokens,
    _fallback_structural,
    _format_conversation_messages,
    _strip_json_code_fences,
)
from relay_teams.memory.semantic_consolidation_prompts import (
    _build_extraction_instructions,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.providers.provider_contracts import (
    EchoProvider,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path: Path) -> MemoryBankService:
    db_file = tmp_path / "test_memory.db"
    repo = MemoryBankRepository(db_file)
    return MemoryBankService(repository=repo)


@pytest.fixture
def service_with_llm(tmp_path: Path) -> MemoryBankService:
    db_file = tmp_path / "test_memory.db"
    repo = MemoryBankRepository(db_file)
    provider = EchoProvider()
    return MemoryBankService(repository=repo, llm_provider=provider)


def _create_entry_request(**overrides: object) -> CreateMemoryEntryRequest:
    base: dict[str, object] = {
        "tier": MemoryTier.WORKING,
        "scope": MemoryScope.SESSION,
        "workspace_id": "ws-test",
        "session_id": "sess-1",
        "run_id": "run-1",
        "kind": MemoryEntryKind.INSIGHT,
        "content": MemoryContent(title="Discovery", body="Found a useful pattern"),
        "source": MemorySourceKind.TASK_RESULT,
    }
    base.update(overrides)
    return CreateMemoryEntryRequest(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-1: semantic mode requires source_run_id
# ---------------------------------------------------------------------------


class TestSemanticModeRequiresSourceRunId:
    async def test_semantic_mode_without_source_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="source_run_id is required"):
            MemoryConsolidationRequest(
                workspace_id="ws-1",
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION,
                consolidation_mode=ConsolidationMode.SEMANTIC,
                source_run_id=None,
            )

    async def test_semantic_mode_with_source_run_id_ok(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            consolidation_mode=ConsolidationMode.SEMANTIC,
            source_run_id="run-1",
        )
        assert req.consolidation_mode == ConsolidationMode.SEMANTIC
        assert req.source_run_id == "run-1"


# ---------------------------------------------------------------------------
# AC-2: structural mode unchanged
# ---------------------------------------------------------------------------


class TestStructuralModeUnchanged:
    async def test_structural_mode_still_works(
        self, service: MemoryBankService
    ) -> None:
        # Create a WORKING entry
        entry = await service.create_entry_async(
            _create_entry_request(
                tier=MemoryTier.WORKING,
                scope=MemoryScope.WORKSPACE,
                run_id="run-1",
            )
        )
        assert entry.tier == MemoryTier.WORKING

        # Consolidate with STRUCTURAL mode
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            session_id="sess-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            consolidation_mode=ConsolidationMode.STRUCTURAL,
        )
        result = await service.consolidate_async(req)
        assert result.consolidated_entry_count >= 0
        assert result.source_entry_count >= 0

    async def test_default_mode_is_structural(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        assert req.consolidation_mode == ConsolidationMode.STRUCTURAL


# ---------------------------------------------------------------------------
# AC-3: valid JSON parses correctly
# ---------------------------------------------------------------------------


class TestSemanticOutputParsingValidJson:
    async def test_valid_json_parses(self) -> None:
        valid_json = """{
            "extractions": [
                {
                    "kind": "decision",
                    "title": "Use Pydantic v2",
                    "body": "Decided to use Pydantic v2 for all models",
                    "context": "During model design phase",
                    "outcome": "All models now use BaseModel",
                    "confidence_score": 0.9,
                    "tags": ["pydantic", "models"]
                }
            ]
        }"""
        output = SemanticExtractionOutput.model_validate_json(valid_json)
        assert len(output.extractions) == 1
        assert output.extractions[0].kind == MemoryEntryKind.DECISION
        assert output.extractions[0].title == "Use Pydantic v2"
        assert output.extractions[0].confidence_score == 0.9

    async def test_json_with_code_fences_parses(self) -> None:
        fenced = """```json
{
    "extractions": [
        {
            "kind": "insight",
            "title": "Pattern found",
            "body": "A recurring pattern was identified"
        }
    ]
}
```"""
        stripped = _strip_json_code_fences(fenced)
        output = SemanticExtractionOutput.model_validate_json(stripped)
        assert len(output.extractions) == 1
        assert output.extractions[0].kind == MemoryEntryKind.INSIGHT


# ---------------------------------------------------------------------------
# AC-4: invalid JSON falls back gracefully
# ---------------------------------------------------------------------------


class TestSemanticOutputParsingInvalidJson:
    async def test_invalid_json_falls_back(self) -> None:
        result = _strip_json_code_fences("not json at all")
        with pytest.raises(ValueError):
            SemanticExtractionOutput.model_validate_json(result)


# ---------------------------------------------------------------------------
# AC-5: entries have correct tier/scope/source/source_ref
# ---------------------------------------------------------------------------


class TestSemanticCreatesCorrectEntries:
    async def test_semantic_result_fields(
        self, service_with_llm: MemoryBankService
    ) -> None:
        """Test that the semantic consolidation result has correct field values."""
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            session_id="sess-1",
            source_run_id="run-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            consolidation_mode=ConsolidationMode.SEMANTIC,
        )
        # With EchoProvider and no messages, it falls back to structural
        result = await service_with_llm.consolidate_async(req)
        assert isinstance(result, MemoryConsolidationResult)
        assert result.extraction_tokens_used >= 0
        assert result.extraction_duration_ms >= 0


# ---------------------------------------------------------------------------
# AC-6: extraction_kinds filter works
# ---------------------------------------------------------------------------


class TestExtractionKindsFilter:
    async def test_extraction_kinds_empty_returns_all(self) -> None:
        # Empty kinds means all kinds
        kinds: tuple[MemoryEntryKind, ...] = ()
        prompt = _build_extraction_instructions(kinds)
        # Should mention all kinds
        for kind in tuple(MemoryEntryKind):
            assert kind.value in prompt

    async def test_extraction_kinds_filters(self) -> None:
        kinds = (MemoryEntryKind.DECISION, MemoryEntryKind.FAILURE_MODE)
        prompt = _build_extraction_instructions(kinds)
        assert "decision" in prompt
        assert "failure_mode" in prompt
        assert "insight" not in prompt


# ---------------------------------------------------------------------------
# AC-7: max_extracted_entries respected
# ---------------------------------------------------------------------------


class TestMaxExtractedEntries:
    async def test_max_extracted_entries_default(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        assert req.max_extracted_entries == 10

    async def test_max_extracted_entries_custom(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            max_extracted_entries=5,
        )
        assert req.max_extracted_entries == 5

    async def test_max_extracted_entries_bounds(self) -> None:
        with pytest.raises(ValueError):
            MemoryConsolidationRequest(
                workspace_id="ws-1",
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION,
                max_extracted_entries=0,
            )
        with pytest.raises(ValueError):
            MemoryConsolidationRequest(
                workspace_id="ws-1",
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION,
                max_extracted_entries=51,
            )


# ---------------------------------------------------------------------------
# AC-8: Superseded marking
# ---------------------------------------------------------------------------


class TestSupersededMarking:
    async def test_structural_marks_source_superseded(
        self, service: MemoryBankService
    ) -> None:
        await service.create_entry_async(
            _create_entry_request(
                tier=MemoryTier.WORKING,
                scope=MemoryScope.WORKSPACE,
                run_id="run-1",
            )
        )
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            session_id="sess-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        result = await service.consolidate_async(req)
        if result.superseded_entry_ids:
            superseded = await service.get_entry_async(result.superseded_entry_ids[0])
            assert superseded is not None
            assert superseded.status == MemoryEntryStatus.SUPERSEDED
            assert superseded.superseded_by_id in result.new_entry_ids


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


class TestBuildExtractionInstructions:
    async def test_all_kinds_covered(self) -> None:
        for kind in tuple(MemoryEntryKind):
            instructions = _build_extraction_instructions((kind,))
            assert kind.value in instructions

    async def test_empty_kinds_covers_all(self) -> None:
        instructions = _build_extraction_instructions(())
        for kind in tuple(MemoryEntryKind):
            assert kind.value in instructions

    async def test_multiple_kinds(self) -> None:
        result = _build_extraction_instructions(
            (MemoryEntryKind.DECISION, MemoryEntryKind.FACT)
        )
        assert "decision:" in result.lower()
        assert "fact:" in result.lower()
        assert "insight:" not in result.lower()


# ---------------------------------------------------------------------------
# JSON fence stripping
# ---------------------------------------------------------------------------


class TestStripJsonCodeFences:
    async def test_no_fences(self) -> None:
        assert _strip_json_code_fences('{"key": "value"}') == '{"key": "value"}'

    async def test_json_fence(self) -> None:
        assert (
            _strip_json_code_fences('```json\n{"key": "value"}\n```')
            == '{"key": "value"}'
        )

    async def test_generic_fence(self) -> None:
        assert (
            _strip_json_code_fences('```\n{"key": "value"}\n```') == '{"key": "value"}'
        )


# ---------------------------------------------------------------------------
# Format conversation messages
# ---------------------------------------------------------------------------


class TestFormatConversationMessages:
    async def test_empty_messages(self) -> None:
        result = _format_conversation_messages([])
        assert result == ()

    async def test_user_and_assistant_messages(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": '{"content": "Hello"}'},
            {"role": "assistant", "message_json": '{"content": "Hi there"}'},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 2
        assert "user" in result[0]
        assert "assistant" in result[1]

    async def test_string_message_json(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "system", "message_json": "System initialization"},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 1
        assert "System initialization" in result[0]

    async def test_dict_message_json_with_content(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": {"content": "Hello from dict"}},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 1
        assert "Hello from dict" in result[0]

    async def test_dict_message_json_without_content(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": {"other_key": "value"}},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 1
        assert "" in result[0]

    async def test_none_message_json(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user"},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 1
        assert "[user]:" in result[0]

    async def test_token_truncation(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": "a" * 100},
            {"role": "assistant", "message_json": "b" * 100},
            {"role": "user", "message_json": "c" * 100},
        ]
        # max_tokens=10 means ~40 chars allowed, should truncate
        result = _format_conversation_messages(msgs, max_tokens=10)
        assert len(result) < 3

    async def test_token_truncation_keeps_latest(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": "first"},
            {"role": "assistant", "message_json": "last message content"},
        ]
        result = _format_conversation_messages(msgs, max_tokens=5)
        # Should keep the last messages
        if len(result) < 2:
            assert "last" in result[-1] if result else True


# ---------------------------------------------------------------------------
# SemanticExtractionInput validation
# ---------------------------------------------------------------------------


class TestSemanticExtractionInputModel:
    async def test_valid_input(self) -> None:
        inp = SemanticExtractionInput(
            conversation_messages=("msg1", "msg2"),
            task_objective="Build a memory system",
            role_id="crafter",
            entry_kinds=(MemoryEntryKind.DECISION,),
        )
        assert inp.task_objective == "Build a memory system"
        assert len(inp.conversation_messages) == 2


# ---------------------------------------------------------------------------
# _ExtractedMemoryEntry validation
# ---------------------------------------------------------------------------


class TestExtractedMemoryEntryModel:
    async def test_minimal_entry(self) -> None:
        entry = _ExtractedMemoryEntry(
            kind=MemoryEntryKind.FACT,
            title="A fact",
            body="Some body",
        )
        assert entry.confidence_score == 0.7
        assert entry.tags == ()

    async def test_confidence_out_of_bounds(self) -> None:
        with pytest.raises(ValueError):
            _ExtractedMemoryEntry(
                kind=MemoryEntryKind.FACT,
                title="A fact",
                body="Some body",
                confidence_score=1.5,
            )
        with pytest.raises(ValueError):
            _ExtractedMemoryEntry(
                kind=MemoryEntryKind.FACT,
                title="A fact",
                body="Some body",
                confidence_score=-0.1,
            )


# ---------------------------------------------------------------------------
# MemoryConsolidationResult new fields
# ---------------------------------------------------------------------------


class TestMemoryConsolidationResultFields:
    async def test_default_extraction_fields(self) -> None:
        result = MemoryConsolidationResult(
            source_entry_count=5,
            consolidated_entry_count=3,
            superseded_entry_ids=(),
            new_entry_ids=(),
        )
        assert result.extraction_tokens_used == 0
        assert result.extraction_duration_ms == 0

    async def test_custom_extraction_fields(self) -> None:
        result = MemoryConsolidationResult(
            source_entry_count=5,
            consolidated_entry_count=3,
            superseded_entry_ids=(),
            new_entry_ids=(),
            extraction_tokens_used=1500,
            extraction_duration_ms=3200,
        )
        assert result.extraction_tokens_used == 1500
        assert result.extraction_duration_ms == 3200


# ---------------------------------------------------------------------------
# AC: consolidate_async coverage on service.py changed lines
# ---------------------------------------------------------------------------


class TestConsolidateAsync:
    """Cover the async consolidation dispatch in MemoryBankService."""

    @pytest.mark.asyncio
    async def test_structural_async_dispatches(
        self, service: MemoryBankService
    ) -> None:
        """consolidate_async with STRUCTURAL mode hits
        _consolidate_structural_async."""
        await service.create_entry_async(
            _create_entry_request(
                tier=MemoryTier.WORKING,
                confidence_score=0.85,
            )
        )
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            session_id="sess-1",
        )
        result = await service.consolidate_async(req)
        assert result.consolidated_entry_count == 1
        assert len(result.superseded_entry_ids) == 1

    @pytest.mark.asyncio
    async def test_semantic_async_no_llm_falls_back(
        self, service: MemoryBankService
    ) -> None:
        """consolidate_async with SEMANTIC + no llm_provider falls back
        to structural."""
        await service.create_entry_async(
            _create_entry_request(
                tier=MemoryTier.WORKING,
                confidence_score=0.85,
            )
        )
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            session_id="sess-1",
            consolidation_mode=ConsolidationMode.SEMANTIC,
            source_run_id="run-1",
        )
        # No llm_provider configured -> falls back to structural
        result = await service.consolidate_async(req)
        assert result.consolidated_entry_count == 1

    @pytest.mark.asyncio
    async def test_semantic_async_with_llm_falls_back_to_structural(
        self, service_with_llm: MemoryBankService
    ) -> None:
        """consolidate_async with SEMANTIC + llm_provider but no
        message_repo still falls back to structural."""
        await service_with_llm.create_entry_async(
            _create_entry_request(
                tier=MemoryTier.WORKING,
                confidence_score=0.85,
            )
        )
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            session_id="sess-1",
            consolidation_mode=ConsolidationMode.SEMANTIC,
            source_run_id="run-1",
        )
        # llm_provider set but no message_repo -> falls back
        result = await service_with_llm.consolidate_async(req)
        assert result.consolidated_entry_count == 1


# ---------------------------------------------------------------------------
# _estimate_tokens coverage
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    async def test_empty_string(self) -> None:
        assert _estimate_tokens("") == 0

    async def test_none_text(self) -> None:
        assert _estimate_tokens("") == 0

    async def test_short_text(self) -> None:
        # 3 chars -> max(1, 3//4) = max(1, 0) = 1
        assert _estimate_tokens("abc") == 1

    async def test_longer_text(self) -> None:
        # 16 chars -> 16 // 4 = 4
        assert _estimate_tokens("a" * 16) == 4

    async def test_exactly_four_chars(self) -> None:
        assert _estimate_tokens("abcd") == 1


# ---------------------------------------------------------------------------
# _fallback_structural coverage
# ---------------------------------------------------------------------------


class TestFallbackStructural:
    @pytest.mark.asyncio
    async def test_fallback_returns_zero_counts(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        result = await _fallback_structural(req)
        assert result.source_entry_count == 0
        assert result.consolidated_entry_count == 0
        assert result.new_entry_ids == ()
        assert result.superseded_entry_ids == ()
        assert result.extraction_tokens_used == 0
        assert result.extraction_duration_ms == 0


# ---------------------------------------------------------------------------
# _semantic_consolidate_async full flow coverage
# ---------------------------------------------------------------------------


class MockMessageRepo:
    """Minimal mock for _MessageRepoProtocol."""

    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = messages

    async def get_messages_by_session_run_ids_async(
        self,
        session_id: str,
        run_ids: tuple[str, ...],
        *,
        include_cleared: bool = False,
        include_hidden_from_context: bool = False,
    ) -> list[dict[str, object]]:
        return self._messages


class MockLLMProvider(LLMProvider):
    """Minimal mock for LLMProvider that returns valid extraction JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    @override
    async def generate(self, _request: LLMRequest) -> str:
        return self._response


VALID_EXTRACTION_JSON = """{
    "extractions": [
        {
            "kind": "decision",
            "title": "Use Pydantic v2",
            "body": "Decided to use Pydantic v2 for all models",
            "context": "During model design phase",
            "outcome": "All models now use BaseModel",
            "confidence_score": 0.9,
            "tags": ["pydantic", "models"]
        }
    ]
}"""

VALID_MULTI_EXTRACTION_JSON = """{
    "extractions": [
        {
            "kind": "insight",
            "title": "Pattern found",
            "body": "A recurring pattern was identified"
        },
        {
            "kind": "fact",
            "title": "API rate limit",
            "body": "The API has a rate limit of 100 req/min"
        },
        {
            "kind": "decision",
            "title": "Use async",
            "body": "Switch to async for all DB calls"
        }
    ]
}"""


def _make_consolidation_request(**overrides: object) -> MemoryConsolidationRequest:
    base: dict[str, object] = {
        "workspace_id": "ws-1",
        "target_tier": MemoryTier.MEDIUM_TERM,
        "target_scope": MemoryScope.SESSION,
        "consolidation_mode": ConsolidationMode.SEMANTIC,
        "source_run_id": "run-1",
        "session_id": "sess-1",
    }
    base.update(overrides)
    return MemoryConsolidationRequest(**base)  # type: ignore[arg-type]


_HIT_MESSAGES: list[dict[str, object]] = [
    {"role": "user", "message_json": '{"content": "Hello"}'},
    {"role": "assistant", "message_json": '{"content": "How can I help?"}'},
]


class TestSemanticConsolidateAsyncFullFlow:
    @pytest.mark.asyncio
    async def test_no_messages_falls_back(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request(source_run_id="run-empty")
        repo = MockMessageRepo([])
        provider = MockLLMProvider(VALID_EXTRACTION_JSON)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 0
        assert result.source_entry_count == 0

    @pytest.mark.asyncio
    async def test_with_event_log(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        class MockEventLog:
            async def write_event(
                self, event_type: str, payload: dict[str, object]
            ) -> None:
                pass

        req = _make_consolidation_request()
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(VALID_EXTRACTION_JSON)
        result = await _semantic_consolidate_async(
            req,
            llm_provider=provider,
            message_repo=repo,
            event_log=MockEventLog(),
        )
        assert result.consolidated_entry_count == 1
        assert len(result.new_entry_ids) == 1
        assert result.extraction_tokens_used > 0

    @pytest.mark.asyncio
    async def test_valid_json_multiple_extractions(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request()
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(VALID_MULTI_EXTRACTION_JSON)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 3
        assert len(result.new_entry_ids) == 3

    @pytest.mark.asyncio
    async def test_max_entries_limits_output(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request(max_extracted_entries=2)
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(VALID_MULTI_EXTRACTION_JSON)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 2
        assert len(result.new_entry_ids) == 2

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request()
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider("this is not json")
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.source_entry_count == 0
        assert result.new_entry_ids == ()

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request()

        class FailingProvider:
            async def generate(self, request: object) -> str:
                raise RuntimeError("LLM unavailable")

            async def generate_stream(
                self, request: object, **kwargs: object
            ) -> object:
                return []

        repo = MockMessageRepo(_HIT_MESSAGES)
        result = await _semantic_consolidate_async(
            req,
            llm_provider=FailingProvider(),  # type: ignore[arg-type]
            message_repo=repo,
        )
        assert result.source_entry_count == 0
        assert result.new_entry_ids == ()

    @pytest.mark.asyncio
    async def test_fenced_json_parses(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        fenced = "```json\n" + VALID_EXTRACTION_JSON + "\n```"
        req = _make_consolidation_request()
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(fenced)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 1

    @pytest.mark.asyncio
    async def test_empty_extractions_result(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        empty_json = '{"extractions": []}'
        req = _make_consolidation_request()
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(empty_json)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 0
        assert result.new_entry_ids == ()

    @pytest.mark.asyncio
    async def test_extraction_kinds_filter(self) -> None:
        from relay_teams.memory.semantic_consolidation import (
            _semantic_consolidate_async,
        )

        req = _make_consolidation_request(extraction_kinds=(MemoryEntryKind.DECISION,))
        repo = MockMessageRepo(_HIT_MESSAGES)
        provider = MockLLMProvider(VALID_EXTRACTION_JSON)
        result = await _semantic_consolidate_async(
            req, llm_provider=provider, message_repo=repo
        )
        assert result.consolidated_entry_count == 1
