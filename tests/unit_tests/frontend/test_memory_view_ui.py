# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_memory_search_payload_preserves_any_status() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert "payload.status = memoryState.status || null;" in source
    assert "if (memoryState.status) {\n        payload.status" not in source


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
    ]:
        assert source.count(f"'{key}'") == 2


def test_memory_bank_architecture_doc_was_renamed() -> None:
    new_path = Path("docs/modules/memory/memory-bank-architecture.md")
    old_path = Path("docs/design/fe1-memory-bank.md")
    source = new_path.read_text(encoding="utf-8")

    assert new_path.exists()
    assert not old_path.exists()
    assert source.startswith("# Memory Bank Architecture")
