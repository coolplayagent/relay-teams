from __future__ import annotations

import pytest


class TestExtractPathsFromCommand:
    def test_extract_cd_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_rm_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_mkdir_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("mkdir -p /tmp/newdir/subdir")
        assert "/tmp/newdir/subdir" in paths

    def test_extract_multiple_commands(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd /project")
        assert "/project" in paths

        paths = extract_paths_from_command("rm -rf /project/build")
        assert "/project/build" in paths

        paths = extract_paths_from_command("mkdir /project/dist")
        assert "/project/dist" in paths

    def test_ignore_flags(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf -force /tmp/test")

        assert "/tmp/test" in paths

    def test_quoted_paths(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd '/path with spaces'")

        assert "'/path with spaces'" in paths or "/path with spaces" in paths


class TestNormalizeTimeout:
    def test_none_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(None)
        assert result == 30000

    def test_custom_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(60000)
        assert result == 60000

    def test_timeout_too_large(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(200000)
        assert result == 120000

    def test_timeout_too_small(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(0)

    def test_negative_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(-1)
