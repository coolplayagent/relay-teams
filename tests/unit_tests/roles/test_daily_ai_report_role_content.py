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


def test_daily_ai_report_role_includes_block_ai_sources() -> None:
    role_path = get_builtin_roles_dir() / "daily-ai-report.md"

    content = role_path.read_text(encoding="utf-8")

    assert "- [Block AI](https://block.xyz/ai)" in content
    assert "- [Block AI News](https://block.xyz/news/ai)" in content
    assert "RSS: <https://engineering.block.xyz/blog/rss.xml>" in content
    assert "- `blocks`" in content


def test_daily_ai_report_role_includes_ai_engineer_talk_sources() -> None:
    role_path = get_builtin_roles_dir() / "daily-ai-report.md"

    content = role_path.read_text(encoding="utf-8")

    assert "- [AI Engineer](https://www.ai.engineer/)" in content
    assert "- [AI Engineer Europe](https://www.ai.engineer/europe)" in content
    assert "## 会议与演讲内容" in content
    assert "Site: <https://www.ai.engineer/>" in content
    assert "Site: <https://www.ai.engineer/europe>" in content
