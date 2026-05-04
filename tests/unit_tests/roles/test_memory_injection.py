# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryQuery,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_injection import (
    _build_project_memory_section,
    build_role_with_memory,
)
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import MemoryProfile, RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry


def _make_role(**overrides: object) -> RoleDefinition:
    base: dict[str, object] = {
        "role_id": "crafter",
        "version": "1.0.0",
        "name": "Crafter",
        "description": "Test role",
        "system_prompt": "You are a test assistant.",
        "memory_profile": MemoryProfile(enabled=True),
    }
    base.update(overrides)
    return RoleDefinition(**base)  # type: ignore[arg-type]


def _create_entry(
    service: MemoryBankService, tier: MemoryTier, **overrides: object
) -> None:
    base: dict[str, object] = {
        "tier": tier,
        "scope": MemoryScope.WORKSPACE,
        "workspace_id": "ws-1",
        "role_id": "crafter",
        "kind": MemoryEntryKind.INSIGHT,
        "content": MemoryContent(title="Test insight", body="Some body text"),
        "source": MemorySourceKind.MANUAL,
    }
    base.update(overrides)
    service.create_entry(CreateMemoryEntryRequest(**base))  # type: ignore[arg-type]


class TestBuildRoleWithMemory:
    def test_skips_coordinator_role(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = True
        role = _make_role()
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=None,
            role=role,
            role_id="coordinator",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    def test_skips_disabled_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role(memory_profile=MemoryProfile(enabled=False))
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=None,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    def test_skips_when_no_services(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=None,
            memory_bank_service=None,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    def test_appends_reflection_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        mock_role_memory = create_autospec(RoleMemoryService, instance=True)
        mock_role_memory.build_injected_memory.return_value = "Past lessons"
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=mock_role_memory,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert "Reflection Memory" in result.system_prompt
        assert "Past lessons" in result.system_prompt

    def test_appends_project_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        _create_entry(service, MemoryTier.PERSISTENT)
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=None,
            memory_bank_service=service,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert "Project Memory" in result.system_prompt

    def test_no_append_when_empty_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        mock_role_memory = create_autospec(RoleMemoryService, instance=True)
        mock_role_memory.build_injected_memory.return_value = ""
        result = build_role_with_memory(
            role_registry=registry,
            role_memory_service=mock_role_memory,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt


class TestBuildProjectMemorySection:
    def test_returns_text_for_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        _create_entry(service, MemoryTier.PERSISTENT)
        result = _build_project_memory_section(
            memory_bank_service=service,
            workspace_id="ws-1",
            role_id="crafter",
        )
        assert "Persistent" in result
        assert "Test insight" in result

    def test_returns_empty_when_no_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        result = _build_project_memory_section(
            memory_bank_service=service,
            workspace_id="ws-1",
        )
        assert result == ""

    def test_handles_service_exception(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        original_list = service.list_entries
        call_count = 0

        def failing_list(query: MemoryQuery) -> object:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("db error")
            return original_list(query)

        service.list_entries = failing_list  # type: ignore[assignment]
        result = _build_project_memory_section(
            memory_bank_service=service,
            workspace_id="ws-1",
        )
        assert result == ""

    def test_includes_medium_term_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        _create_entry(
            service,
            MemoryTier.MEDIUM_TERM,
            scope=MemoryScope.SESSION,
            session_id="s1",
        )
        result = _build_project_memory_section(
            memory_bank_service=service,
            workspace_id="ws-1",
            role_id="crafter",
        )
        assert "Medium Term" in result
