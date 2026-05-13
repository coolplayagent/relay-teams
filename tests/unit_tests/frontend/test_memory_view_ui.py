# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_memory_search_payload_preserves_any_status() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert "payload.status = memoryState.status || null;" in source
    assert "if (memoryState.status) {\n        payload.status" not in source


def test_memory_toolbar_restores_search_after_feature_switch() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert ".classList?.remove('is-hidden')" in source
    assert "data-memory-search" in source
    assert "searchMemoryRows()" in source
    assert "return await searchMemories(payload);" in source
    assert "event.key === 'Enter'" in source
    assert "applyToolbarFilters();" in source


def test_memory_view_renders_architecture_map() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert "function renderMemoryArchitectureMap()" in source
    assert "memory-architecture-map" in source
    assert "memory-architecture-flow" in source
    assert "feature.memory.arch.capture_sources" in source
    assert "feature.memory.arch.reuse_targets" in source
    assert "feature.memory.arch.working.title" in source
    assert "feature.memory.arch.medium.title" in source
    assert "feature.memory.arch.persistent.title" in source


def test_memory_view_renders_skill_draft_workflow() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )
    api_source = Path("frontend/dist/js/core/api/memories.js").read_text(
        encoding="utf-8"
    )
    css_source = Path("frontend/dist/css/components/memory.css").read_text(
        encoding="utf-8"
    )

    assert 'data-memory-tab="skill-drafts"' in source
    assert "generateMemorySkillDrafts(payload)" in source
    assert "validateMemorySkillDraft(draftId)" in source
    assert "applyMemorySkillDraft(draftId)" in source
    assert "updateMemorySkillDraft(draftId, payload)" in source
    assert "feature.memory.drafts.reject_failed" in source
    assert "draftWorkspaceId || memoryState.workspaceId" not in source
    assert "draft.status !== 'applied' && draft.status !== 'applying'" in source
    assert "function reloadSkillDraftRowsIfActive()" in source
    assert "memoryState.activeTab !== 'skill-drafts'" in source
    assert "data-draft-instructions]')?.value || '').trimEnd()" in source
    assert "scopeKind: memoryState.draftScopeKind" in source
    assert (
        "draftKind: memoryState.draftKind === 'auto' ? '' : memoryState.draftKind"
        in source
    )
    assert "feature.memory.drafts.validate_failed" in source
    assert "data-memory-evolve-target" not in source
    assert "renderMemoryEvolutionPanel" not in source
    assert "/api/memories/skill-drafts:generate" in api_source
    assert "memory-draft-shell" in css_source
    assert "memory-draft-editor" in css_source


def test_memory_detail_exposes_lifecycle_fields() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert "feature.memory.status" in source
    assert "feature.memory.source" in source
    assert "feature.memory.expires" in source
    assert "function formatExpiry(value)" in source


def test_memory_architecture_i18n_keys_exist() -> None:
    source = Path("frontend/dist/js/utils/i18n.js").read_text(encoding="utf-8")

    for key in [
        "feature.memory.arch.title",
        "feature.memory.arch.capture",
        "feature.memory.arch.capture_sources",
        "feature.memory.arch.consolidation",
        "feature.memory.arch.reuse",
        "feature.memory.arch.reuse_targets",
        "feature.memory.arch.working.title",
        "feature.memory.arch.medium.title",
        "feature.memory.arch.persistent.title",
        "feature.memory.source",
        "feature.memory.expires",
        "feature.memory.no_expiry",
        "feature.memory.entries_tab",
        "feature.memory.skill_drafts_tab",
        "feature.memory.drafts.generate",
        "feature.memory.drafts.validate",
        "feature.memory.drafts.validate_failed",
        "feature.memory.drafts.apply",
        "feature.memory.drafts.reject_failed",
    ]:
        assert source.count(f"'{key}'") == 2


def test_memory_chinese_copy_uses_ji_yi() -> None:
    source = Path("frontend/dist/js/utils/i18n.js").read_text(encoding="utf-8")
    feature_start = source.index("'feature.memory.title': '记忆'")
    feature_end = source.index("});", feature_start)
    zh_feature_block = source[feature_start:feature_end]

    assert "'sidebar.feature_memory': '记忆'" in source
    assert "'subagent.memory_empty': '暂无记忆条目。'" in source
    assert "'feature.memory.title': '记忆'" in zh_feature_block
    assert "'feature.memory.loading': '正在加载记忆...'" in zh_feature_block
    assert "'feature.memory.empty': '暂无记忆条目'" in zh_feature_block
    assert "'feature.memory.search_placeholder': '搜索记忆'" in zh_feature_block
    assert "'feature.memory.arch.title': '记忆架构'" in zh_feature_block
    assert "Memory Bank" not in zh_feature_block
    assert "搜索 memory" not in zh_feature_block


def test_memory_bank_architecture_doc_was_renamed() -> None:
    new_path = Path("docs/modules/memory/memory-bank-architecture.md")
    old_path = Path("docs/design/fe1-memory-bank.md")
    source = new_path.read_text(encoding="utf-8")

    assert new_path.exists()
    assert not old_path.exists()
    assert source.startswith("# Memory Bank Architecture")
