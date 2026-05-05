# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)
from relay_teams.external_agents.native_config import (
    NativeConfigContent,
    NativeConfigGenerator,
    assemble_native_config_content,
    resolve_native_config_filename,
)
from relay_teams.external_agents.skill_bridge import (
    BridgedSkill,
    SkillBridgeManifest,
)
from relay_teams.roles.role_models import RoleDefinition


# ---------------------------------------------------------------------------
# resolve_native_config_filename
# ---------------------------------------------------------------------------


def test_resolve_filename_anthropic() -> None:
    assert resolve_native_config_filename("anthropic") == "CLAUDE.md"


def test_resolve_filename_google() -> None:
    assert resolve_native_config_filename("google") == "GEMINI.md"


def test_resolve_filename_openai() -> None:
    assert resolve_native_config_filename("openai") == "AGENTS.md"


def test_resolve_filename_unknown_defaults_to_agents() -> None:
    assert resolve_native_config_filename("") == "AGENTS.md"
    assert resolve_native_config_filename("custom") == "AGENTS.md"


# ---------------------------------------------------------------------------
# assemble_native_config_content
# ---------------------------------------------------------------------------


def test_assemble_all_parts() -> None:
    content = NativeConfigContent(
        project_instructions="project rules",
        role_prompt="act as designer",
        task_objective="fix bug",
        skill_references="skills here",
        workspace_context="/tmp/ws",
    )
    result = assemble_native_config_content(content)
    assert "project rules" in result
    assert "## Role Definition" in result
    assert "act as designer" in result
    assert "## Task Objective" in result
    assert "fix bug" in result
    assert "skills here" in result
    assert "## Workspace" in result
    assert "/tmp/ws" in result


def test_assemble_empty_parts_omitted() -> None:
    content = NativeConfigContent(
        role_prompt="only this",
    )
    result = assemble_native_config_content(content)
    assert result == "## Role Definition\n\nonly this"


# ---------------------------------------------------------------------------
# NativeConfigGenerator.generate
# ---------------------------------------------------------------------------


def _make_instruction_resolver(tmp_path: Path):
    """Create a minimal PromptInstructionResolver that loads from tmp_path."""
    from relay_teams.agents.execution.prompt_instructions import (
        PromptInstructionResolver,
    )

    return PromptInstructionResolver(app_config_dir=tmp_path / "nonexistent_config")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _minimal_role() -> RoleDefinition:
    return RoleDefinition(
        role_id="test_role",
        name="Test Role",
        description="A test role",
        version="1.0.0",
        system_prompt="You are a test assistant.",
    )


@pytest.mark.asyncio
async def test_generate_anthropic_creates_claude_md(workspace: Path) -> None:
    resolver = _make_instruction_resolver(workspace)
    agent = ExternalAgentConfig(
        agent_id="ext-1",
        name="Ext",
        protocol=ExternalAgentProtocol.ACP,
        transport=StdioTransportConfig(command="agent"),
        native_config_enabled=True,
        native_config_provider="anthropic",
    )
    gen = NativeConfigGenerator(instruction_resolver=resolver)
    spec = await gen.generate(
        agent=agent,
        workspace_path=workspace,
        role=_minimal_role(),
        task_objective="do thing",
    )
    assert spec.files == ("CLAUDE.md",)
    written = (spec.config_dir / "CLAUDE.md").read_text()
    assert "You are a test assistant." in written
    assert "do thing" in written


@pytest.mark.asyncio
async def test_generate_google_creates_gemini_md(workspace: Path) -> None:
    resolver = _make_instruction_resolver(workspace)
    agent = ExternalAgentConfig(
        agent_id="ext-2",
        name="Ext",
        protocol=ExternalAgentProtocol.ACP,
        transport=StdioTransportConfig(command="agent"),
        native_config_enabled=True,
        native_config_provider="google",
    )
    gen = NativeConfigGenerator(instruction_resolver=resolver)
    spec = await gen.generate(
        agent=agent,
        workspace_path=workspace,
        role=_minimal_role(),
        task_objective="do thing",
    )
    assert spec.files == ("GEMINI.md",)


@pytest.mark.asyncio
async def test_generate_default_creates_agents_md(workspace: Path) -> None:
    resolver = _make_instruction_resolver(workspace)
    agent = ExternalAgentConfig(
        agent_id="ext-3",
        name="Ext",
        protocol=ExternalAgentProtocol.ACP,
        transport=StdioTransportConfig(command="agent"),
        native_config_enabled=True,
        native_config_provider="",
    )
    gen = NativeConfigGenerator(instruction_resolver=resolver)
    spec = await gen.generate(
        agent=agent,
        workspace_path=workspace,
        role=_minimal_role(),
        task_objective="do thing",
    )
    assert spec.files == ("AGENTS.md",)


@pytest.mark.asyncio
async def test_generate_includes_skill_bridge_manifest(workspace: Path) -> None:
    resolver = _make_instruction_resolver(workspace)
    agent = ExternalAgentConfig(
        agent_id="ext-4",
        name="Ext",
        protocol=ExternalAgentProtocol.ACP,
        transport=StdioTransportConfig(command="agent"),
        native_config_enabled=True,
    )
    manifest = SkillBridgeManifest(
        skills=(
            BridgedSkill(
                name="code-review",
                description="Review code quality",
                usage_example="After generating code",
            ),
        ),
    )
    gen = NativeConfigGenerator(instruction_resolver=resolver)
    spec = await gen.generate(
        agent=agent,
        workspace_path=workspace,
        role=_minimal_role(),
        task_objective="do thing",
        skill_bridge_manifest=manifest,
    )
    written = (spec.config_dir / "AGENTS.md").read_text()
    assert "code-review" in written
    assert "Review code quality" in written


def test_cleanup_removes_directory(workspace: Path) -> None:
    config_dir = workspace / ".relay-teams" / "external" / "cleanup-test"
    config_dir.mkdir(parents=True)
    (config_dir / "AGENTS.md").write_text("test", encoding="utf-8")
    NativeConfigGenerator.cleanup(config_dir)
    assert not config_dir.exists()


def test_cleanup_noop_on_missing_dir(workspace: Path) -> None:
    missing = workspace / "nonexistent"
    NativeConfigGenerator.cleanup(missing)  # should not raise


# ---------------------------------------------------------------------------
# ExternalAgentConfig new fields
# ---------------------------------------------------------------------------


def test_external_agent_config_native_config_defaults() -> None:
    agent = ExternalAgentConfig(
        agent_id="a1",
        name="A",
        transport=StdioTransportConfig(command="ag"),
    )
    assert agent.native_config_enabled is False
    assert agent.native_config_provider == ""
    assert agent.skill_bridge_enabled is False
    assert agent.skill_bridge_skills == ()
    assert agent.skill_bridge_mode == "inline"


def test_external_agent_config_native_config_enabled() -> None:
    agent = ExternalAgentConfig(
        agent_id="a2",
        name="B",
        transport=StreamableHttpTransportConfig(
            url="http://localhost:8080/rpc",
        ),
        protocol=ExternalAgentProtocol.A2A,
        native_config_enabled=True,
        native_config_provider="anthropic",
        skill_bridge_enabled=True,
        skill_bridge_skills=("web-search",),
        skill_bridge_mode="directory",
    )
    assert agent.native_config_provider == "anthropic"
    assert agent.skill_bridge_skills == ("web-search",)
    assert agent.skill_bridge_mode == "directory"
