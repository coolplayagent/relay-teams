# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from relay_teams.roles.role_models import RoleDocumentDraft, RoleValidationResult
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.roles.tool_diet_policy import ToolDietPolicy


def _make_service(
    *,
    policy: ToolDietPolicy | None = None,
) -> RoleSettingsService:
    tool_registry = MagicMock()
    tool_registry.validate_known = MagicMock()
    tool_registry.resolve_known = MagicMock(return_value=("read",))
    mcp_registry = MagicMock()
    mcp_registry.validate_known = MagicMock()
    mcp_registry.resolve_server_names = MagicMock(return_value=())
    skill_registry = MagicMock()
    skill_registry.resolve_known = MagicMock(return_value=())
    return RoleSettingsService(
        roles_dir=Path("/tmp/test_roles"),
        builtin_roles_dir=Path("/tmp/test_builtin"),
        get_tool_registry=lambda: tool_registry,
        get_mcp_registry=lambda: mcp_registry,
        get_skill_registry=lambda: skill_registry,
        get_external_agent_service=None,
        on_roles_reloaded=lambda _: None,
        tool_diet_policy=policy,
    )


def _make_draft(
    *,
    role_id: str = "test_role",
    tools: tuple[str, ...] = ("read",),
    system_prompt: str = "A sufficiently specific system prompt for testing.",
) -> RoleDocumentDraft:
    return RoleDocumentDraft(
        role_id=role_id,
        name="Test Role",
        description="A test role for diet validation",
        version="1",
        tools=tools,
        system_prompt=system_prompt,
    )


class TestToolDietSettingsIntegration:
    def test_tool_count_exceeded_is_warning_not_rejection(self) -> None:
        service = _make_service()
        draft = _make_draft(tools=tuple(f"tool_{i}" for i in range(12)))
        tool_registry = service._get_tool_registry()
        tool_registry.resolve_known = MagicMock(side_effect=lambda tools, **kw: tools)
        tool_registry.validate_known = MagicMock()
        result = service.validate_role_document(draft)
        assert isinstance(result, RoleValidationResult)
        assert result.valid is True
        assert any(f.code == "tool_count_exceeded" for f in result.diet_warnings)

    def test_warnings_passed_through(self) -> None:
        service = _make_service()
        draft = _make_draft(
            tools=tuple(f"tool_{i}" for i in range(7)),
            system_prompt="Handle everything",
        )
        tool_registry = service._get_tool_registry()
        tool_registry.resolve_known = MagicMock(side_effect=lambda tools, **kw: tools)
        tool_registry.validate_known = MagicMock()
        result = service.validate_role_document(draft)
        assert isinstance(result, RoleValidationResult)
        assert result.valid is True
        assert len(result.diet_warnings) >= 1

    def test_no_warnings_ok(self) -> None:
        service = _make_service()
        draft = _make_draft()
        tool_registry = service._get_tool_registry()
        tool_registry.resolve_known = MagicMock(side_effect=lambda tools, **kw: tools)
        tool_registry.validate_known = MagicMock()
        result = service.validate_role_document(draft)
        assert isinstance(result, RoleValidationResult)
        assert result.valid is True
        assert len(result.diet_warnings) == 0
