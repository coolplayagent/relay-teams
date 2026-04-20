# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin import get_builtin_roles_dir


def test_daily_ai_report_role_includes_x_account_sources() -> None:
    role_path = get_builtin_roles_dir() / "daily-ai-report.md"

    content = role_path.read_text(encoding="utf-8")

    assert "## X 账号信源" in content
    assert "- `OpenAI`" in content
    assert "- `AnthropicAI`" in content
    assert "- `karpathy`" in content
    assert "- `AndrewYNg`" in content
