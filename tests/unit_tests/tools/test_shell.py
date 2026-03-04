from __future__ import annotations

import pytest


class TestExtractPathsFromCommand:
    """测试命令路径提取"""

    def test_extract_cd_path(self):
        """测试 cd 命令路径提取"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_rm_path(self):
        """测试 rm 命令路径提取"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_mkdir_path(self):
        """测试 mkdir 命令路径提取"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("mkdir -p /tmp/newdir/subdir")
        assert "/tmp/newdir/subdir" in paths

    def test_extract_multiple_commands(self):
        """测试多命令路径提取"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        # cd 命令
        paths = extract_paths_from_command("cd /project")
        assert "/project" in paths

        # rm 命令
        paths = extract_paths_from_command("rm -rf /project/build")
        assert "/project/build" in paths

        # mkdir 命令
        paths = extract_paths_from_command("mkdir /project/dist")
        assert "/project/dist" in paths

    def test_ignore_flags(self):
        """测试忽略标志参数"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf -force /tmp/test")

        assert "/tmp/test" in paths

    def test_quoted_paths(self):
        """测试带引号的路径"""
        from agent_teams.tools.workspace.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd '/path with spaces'")

        assert "'/path with spaces'" in paths or "/path with spaces" in paths


class TestNormalizeTimeout:
    """测试超时标准化"""

    def test_none_timeout(self):
        """测试 None 超时"""
        from agent_teams.tools.workspace.shell_executor import normalize_timeout

        result = normalize_timeout(None)
        assert result == 30000  # DEFAULT_TIMEOUT_SECONDS * 1000

    def test_custom_timeout(self):
        """测试自定义超时"""
        from agent_teams.tools.workspace.shell_executor import normalize_timeout

        result = normalize_timeout(60000)
        assert result == 60000

    def test_timeout_too_large(self):
        """测试超时过大"""
        from agent_teams.tools.workspace.shell_executor import normalize_timeout

        result = normalize_timeout(200000)  # > MAX_TIMEOUT_SECONDS * 1000
        assert result == 120000  # MAX_TIMEOUT_SECONDS * 1000

    def test_timeout_too_small(self):
        """测试超时过小"""
        from agent_teams.tools.workspace.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(0)

    def test_negative_timeout(self):
        """测试负数超时"""
        from agent_teams.tools.workspace.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(-1)
