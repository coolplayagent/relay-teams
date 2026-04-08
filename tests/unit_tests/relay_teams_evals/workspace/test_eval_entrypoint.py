from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh is required")
def test_eval_entrypoint_copies_only_whitelisted_config_entries(tmp_path: Path) -> None:
    staging_dir = tmp_path / "staging"
    target_dir = tmp_path / "target"
    staging_dir.mkdir()
    target_dir.mkdir()

    (staging_dir / "model.json").write_text('{"model":"demo"}\n', encoding="utf-8")
    (staging_dir / "notifications.json").write_text("{}", encoding="utf-8")
    (staging_dir / "orchestration.json").write_text("{}", encoding="utf-8")
    (staging_dir / ".env").write_text("HTTP_PROXY=http://proxy\n", encoding="utf-8")
    (staging_dir / "mcp.json").write_text("{}", encoding="utf-8")
    (staging_dir / "logger.ini").write_text("[loggers]\n", encoding="utf-8")
    (staging_dir / "relay_teams.db").write_text("sqlite", encoding="utf-8")
    (staging_dir / "roles").mkdir()
    (staging_dir / "skills").mkdir()
    (staging_dir / "roles" / "custom.md").write_text("role", encoding="utf-8")
    (staging_dir / "skills" / "demo.txt").write_text("skill", encoding="utf-8")
    (staging_dir / "log").mkdir()
    (staging_dir / "log" / "backend.log").write_text("host-log", encoding="utf-8")
    (staging_dir / "secrets.txt").write_text("secret", encoding="utf-8")

    script_path = (
        Path(__file__).resolve().parents[4] / "docker" / "eval-entrypoint.sh"
    ).as_posix()
    env = os.environ.copy()
    env["AGENT_TEAMS_CONFIG_STAGING"] = staging_dir.resolve().as_posix()
    env["AGENT_TEAMS_CONFIG_TARGET"] = target_dir.resolve().as_posix()

    subprocess.run(
        [shutil.which("sh") or "sh", script_path, "sh", "-c", "exit 0"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert (target_dir / "model.json").exists()
    assert (target_dir / "notifications.json").exists()
    assert (target_dir / "orchestration.json").exists()
    assert (target_dir / ".env").exists()
    assert (target_dir / "mcp.json").exists()
    assert (target_dir / "logger.ini").exists()
    assert (target_dir / "roles" / "custom.md").exists()
    assert (target_dir / "skills" / "demo.txt").exists()
    assert not (target_dir / "relay_teams.db").exists()
    assert not (target_dir / "log").exists()
    assert not (target_dir / "secrets.txt").exists()
