# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, create_autospec

import pytest

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_injection import (
    _build_project_memory_section_async,
    build_role_with_memory_async,
)
from relay_teams.roles.role_models import MemoryProfile, RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry

pytestmark = pytest.mark.asyncio


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


async def _create_entry(
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
    await service.create_entry_async(CreateMemoryEntryRequest(**base))  # type: ignore[arg-type]


class TestBuildRoleWithMemory:
    async def test_skips_coordinator_role(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = True
        role = _make_role()
        result = await build_role_with_memory_async(
            role_registry=registry,
            role=role,
            role_id="coordinator",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    async def test_skips_disabled_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role(memory_profile=MemoryProfile(enabled=False))
        result = await build_role_with_memory_async(
            role_registry=registry,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    async def test_skips_when_no_services(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        result = await build_role_with_memory_async(
            role_registry=registry,
            memory_bank_service=None,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt

    async def test_appends_project_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        await _create_entry(service, MemoryTier.PERSISTENT)
        result = await build_role_with_memory_async(
            role_registry=registry,
            memory_bank_service=service,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert "Project Memory" in result.system_prompt

    async def test_no_append_when_empty_memory(self, tmp_path: Path) -> None:
        registry = create_autospec(RoleRegistry, instance=True)
        registry.is_coordinator_role.return_value = False
        role = _make_role()
        repo = MemoryBankRepository(tmp_path / "empty.db")
        service = MemoryBankService(repository=repo)
        result = await build_role_with_memory_async(
            role_registry=registry,
            memory_bank_service=service,
            role=role,
            role_id="crafter",
            workspace_id="ws-1",
        )
        assert result.system_prompt == role.system_prompt


class TestBuildProjectMemorySection:
    async def test_returns_text_for_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        await _create_entry(service, MemoryTier.PERSISTENT)
        result = await _build_project_memory_section_async(
            memory_bank_service=service,
            workspace_id="ws-1",
            role_id="crafter",
        )
        assert "Persistent" in result
        assert "Test insight" in result

    async def test_returns_empty_when_no_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        result = await _build_project_memory_section_async(
            memory_bank_service=service,
            workspace_id="ws-1",
        )
        assert result == ""

    async def test_handles_service_exception(self, tmp_path: Path) -> None:
        service = create_autospec(MemoryBankService, instance=True)
        service.list_entries_async = AsyncMock(side_effect=RuntimeError("db error"))
        result = await _build_project_memory_section_async(
            memory_bank_service=service,
            workspace_id="ws-1",
        )
        assert result == ""

    async def test_includes_medium_term_entries(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test.db")
        service = MemoryBankService(repository=repo)
        await _create_entry(
            service,
            MemoryTier.MEDIUM_TERM,
            scope=MemoryScope.SESSION,
            session_id="s1",
        )
        result = await _build_project_memory_section_async(
            memory_bank_service=service,
            workspace_id="ws-1",
            role_id="crafter",
        )
        assert "Medium Term" in result
