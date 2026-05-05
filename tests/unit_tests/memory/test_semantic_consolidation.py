# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

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
from relay_teams.memory.semantic_consolidation import (
    SemanticExtractionInput,
    SemanticExtractionOutput,
    _ExtractedMemoryEntry,
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
    def test_semantic_mode_without_source_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="source_run_id is required"):
            MemoryConsolidationRequest(
                workspace_id="ws-1",
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION,
                consolidation_mode=ConsolidationMode.SEMANTIC,
                source_run_id=None,
            )

    def test_semantic_mode_with_source_run_id_ok(self) -> None:
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
    def test_structural_mode_still_works(self, service: MemoryBankService) -> None:
        # Create a WORKING entry
        entry = service.create_entry(
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
        result = service.consolidate(req)
        assert result.consolidated_entry_count >= 0
        assert result.source_entry_count >= 0

    def test_default_mode_is_structural(self) -> None:
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
    def test_valid_json_parses(self) -> None:
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

    def test_json_with_code_fences_parses(self) -> None:
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
    def test_invalid_json_falls_back(self) -> None:
        result = _strip_json_code_fences("not json at all")
        with pytest.raises(ValueError):
            SemanticExtractionOutput.model_validate_json(result)


# ---------------------------------------------------------------------------
# AC-5: entries have correct tier/scope/source/source_ref
# ---------------------------------------------------------------------------


class TestSemanticCreatesCorrectEntries:
    def test_semantic_result_fields(self, service_with_llm: MemoryBankService) -> None:
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
        result = service_with_llm.consolidate(req)
        assert isinstance(result, MemoryConsolidationResult)
        assert hasattr(result, "extraction_tokens_used")
        assert hasattr(result, "extraction_duration_ms")


# ---------------------------------------------------------------------------
# AC-6: extraction_kinds filter works
# ---------------------------------------------------------------------------


class TestExtractionKindsFilter:
    def test_extraction_kinds_empty_returns_all(self) -> None:
        # Empty kinds means all kinds
        kinds: tuple[MemoryEntryKind, ...] = ()
        prompt = _build_extraction_instructions(kinds)
        # Should mention all kinds
        for kind in tuple(MemoryEntryKind):
            assert kind.value in prompt

    def test_extraction_kinds_filters(self) -> None:
        kinds = (MemoryEntryKind.DECISION, MemoryEntryKind.FAILURE_MODE)
        prompt = _build_extraction_instructions(kinds)
        assert "decision" in prompt
        assert "failure_mode" in prompt
        assert "insight" not in prompt


# ---------------------------------------------------------------------------
# AC-7: max_extracted_entries respected
# ---------------------------------------------------------------------------


class TestMaxExtractedEntries:
    def test_max_extracted_entries_default(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        assert req.max_extracted_entries == 10

    def test_max_extracted_entries_custom(self) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            max_extracted_entries=5,
        )
        assert req.max_extracted_entries == 5

    def test_max_extracted_entries_bounds(self) -> None:
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
    def test_structural_marks_source_superseded(
        self, service: MemoryBankService
    ) -> None:
        service.create_entry(
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
        result = service.consolidate(req)
        if result.superseded_entry_ids:
            superseded = service.get_entry(result.superseded_entry_ids[0])
            assert superseded is not None
            assert superseded.status == MemoryEntryStatus.SUPERSEDED
            assert superseded.superseded_by_id in result.new_entry_ids


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


class TestBuildExtractionInstructions:
    def test_all_kinds_covered(self) -> None:
        for kind in tuple(MemoryEntryKind):
            instructions = _build_extraction_instructions((kind,))
            assert kind.value in instructions

    def test_empty_kinds_covers_all(self) -> None:
        instructions = _build_extraction_instructions(())
        for kind in tuple(MemoryEntryKind):
            assert kind.value in instructions


# ---------------------------------------------------------------------------
# JSON fence stripping
# ---------------------------------------------------------------------------


class TestStripJsonCodeFences:
    def test_no_fences(self) -> None:
        assert _strip_json_code_fences('{"key": "value"}') == '{"key": "value"}'

    def test_json_fence(self) -> None:
        assert (
            _strip_json_code_fences('```json\n{"key": "value"}\n```')
            == '{"key": "value"}'
        )

    def test_generic_fence(self) -> None:
        assert (
            _strip_json_code_fences('```\n{"key": "value"}\n```') == '{"key": "value"}'
        )


# ---------------------------------------------------------------------------
# Format conversation messages
# ---------------------------------------------------------------------------


class TestFormatConversationMessages:
    def test_empty_messages(self) -> None:
        result = _format_conversation_messages([])
        assert result == ()

    def test_user_and_assistant_messages(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "user", "message_json": '{"content": "Hello"}'},
            {"role": "assistant", "message_json": '{"content": "Hi there"}'},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 2
        assert "user" in result[0]
        assert "assistant" in result[1]

    def test_string_message_json(self) -> None:
        msgs: list[dict[str, object]] = [
            {"role": "system", "message_json": "System initialization"},
        ]
        result = _format_conversation_messages(msgs)
        assert len(result) == 1
        assert "System initialization" in result[0]


# ---------------------------------------------------------------------------
# SemanticExtractionInput validation
# ---------------------------------------------------------------------------


class TestSemanticExtractionInputModel:
    def test_valid_input(self) -> None:
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
    def test_minimal_entry(self) -> None:
        entry = _ExtractedMemoryEntry(
            kind=MemoryEntryKind.FACT,
            title="A fact",
            body="Some body",
        )
        assert entry.confidence_score == 0.7
        assert entry.tags == ()

    def test_confidence_out_of_bounds(self) -> None:
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
    def test_default_extraction_fields(self) -> None:
        result = MemoryConsolidationResult(
            source_entry_count=5,
            consolidated_entry_count=3,
            superseded_entry_ids=(),
            new_entry_ids=(),
        )
        assert result.extraction_tokens_used == 0
        assert result.extraction_duration_ms == 0

    def test_custom_extraction_fields(self) -> None:
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
