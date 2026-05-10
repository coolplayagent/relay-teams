# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from relay_teams.agents.orchestration.settings_models import OrchestrationSettings
from relay_teams.builtin.resources import (
    ensure_app_config_bootstrap,
    get_builtin_orchestration_config_path,
    get_builtin_roles_dir,
    get_builtin_skills_dir,
)
from relay_teams.roles.role_registry import RoleLoader


def test_ensure_app_config_bootstrap_seeds_empty_model_config(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"

    ensure_app_config_bootstrap(config_dir)

    model_config_path = config_dir / "model.json"
    assert model_config_path.exists()
    assert json.loads(model_config_path.read_text(encoding="utf-8")) == {}


def test_primary_builtin_runtime_roles_allow_all_mcp_servers_and_skills() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    for role_id in ("MainAgent", "Crafter"):
        role = registry.get(role_id)
        assert role.mcp_servers == ("*",)
        assert role.skills == ("*",)


def test_builtin_orchestration_presets_are_planner_first() -> None:
    settings = OrchestrationSettings.model_validate_json(
        get_builtin_orchestration_config_path().read_text(encoding="utf-8")
    )
    presets = {preset.preset_id: preset for preset in settings.presets}
    default_cycle_budget = presets["default"].policy.max_orchestration_cycles

    for preset_id in ("default", "fast_graph", "standard_graph"):
        preset = presets[preset_id]
        policy = preset.policy
        assert policy.auto_plan_long_tasks is True
        assert policy.planner_role_id == "DelegationPlanner"
        assert policy.coordinator_inline_budget_steps == 0
        assert "DelegationPlanner" in preset.role_ids

        if preset.graph is not None:
            assert policy.max_orchestration_cycles >= default_cycle_budget


def test_deepresearch_news_sources_include_block_ai_source_entries() -> None:
    news_source_path = get_builtin_skills_dir() / "deepresearch" / "news_source.md"

    content = news_source_path.read_text(encoding="utf-8")

    assert "- [Block AI](https://block.xyz/ai)" in content
    assert "- [Block AI News](https://block.xyz/news/ai)" in content
    assert "RSS: <https://engineering.block.xyz/blog/rss.xml>" in content


def test_deepresearch_news_sources_include_ai_engineer_talk_sources() -> None:
    news_source_path = get_builtin_skills_dir() / "deepresearch" / "news_source.md"

    content = news_source_path.read_text(encoding="utf-8")

    assert "- [AI Engineer](https://www.ai.engineer/)" in content
    assert "- [AI Engineer Europe](https://www.ai.engineer/europe)" in content
    assert "## 会议与演讲内容" in content
    assert "Site: <https://www.ai.engineer/>" in content
    assert "Site: <https://www.ai.engineer/europe>" in content
