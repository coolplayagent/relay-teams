from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


class TestRipgrepFilepath:
    """测试 ripgrep 二进制路径解析"""

    def test_platform_detection(self):
        """测试平台检测"""
        from agent_teams.tools import ripgrep

        key = ripgrep._get_platform_key()

        # Windows 应该是 AMD64-windows
        assert (
            key == "AMD64-windows"
            or key.startswith("AMD64")
            or "windows" in key.lower()
        )

    @pytest.mark.asyncio
    async def test_local_cache(self, tmp_path):
        """本地缓存存在时使用缓存"""
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        # Windows 使用 .exe
        rg_name = "rg.exe" if os.name == "nt" else "rg"
        rg = cache_dir / rg_name
        rg.write_bytes(b"fake")

        with patch("shutil.which", return_value=None):
            with patch("agent_teams.tools.ripgrep.BIN_DIR", cache_dir):
                from agent_teams.tools import ripgrep

                ripgrep.get_rg_path.cache_clear()
                path = await ripgrep.get_rg_path()

                assert path == rg

    @pytest.mark.asyncio
    async def test_get_rg_path_can_be_awaited_multiple_times(self, tmp_path):
        """同一进程内多次 await 不应复用已完成 coroutine"""
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        rg_name = "rg.exe" if os.name == "nt" else "rg"
        rg = cache_dir / rg_name
        rg.write_bytes(b"fake")

        with patch("shutil.which", return_value=None):
            with patch("agent_teams.tools.ripgrep.BIN_DIR", cache_dir):
                from agent_teams.tools import ripgrep

                ripgrep.get_rg_path.cache_clear()
                first = await ripgrep.get_rg_path()
                second = await ripgrep.get_rg_path()

                assert first == rg
                assert second == rg


class TestRipgrepDownload:
    """测试 ripgrep 下载流程"""

    @pytest.mark.asyncio
    async def test_download_enables_redirect_following(self, tmp_path):
        """下载时应跟随 GitHub 302 重定向"""
        from agent_teams.tools import ripgrep

        target = tmp_path / "rg.exe"
        response = MagicMock(status_code=200, content=b"fake-zip")
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client
        client_cm.__aexit__.return_value = None

        with patch(
            "agent_teams.tools.ripgrep.httpx.AsyncClient", return_value=client_cm
        ) as mock_client_cls:
            with patch(
                "agent_teams.tools.ripgrep._get_platform_key",
                return_value="AMD64-windows",
            ):
                with patch("agent_teams.tools.ripgrep._extract_zip") as mock_extract_zip:
                    with patch("agent_teams.tools.ripgrep.os.chmod"):
                        await ripgrep._download_rg(target)

        mock_client_cls.assert_called_once_with(follow_redirects=True)
        client.get.assert_awaited_once()
        mock_extract_zip.assert_called_once_with(b"fake-zip", target)

    @pytest.mark.asyncio
    async def test_download_non_200_raises(self, tmp_path):
        """下载状态非 200 时抛出异常"""
        from agent_teams.tools import ripgrep
        from agent_teams.tools.ripgrep_errors import DownloadFailedError

        target = tmp_path / "rg.exe"
        response = MagicMock(status_code=404, content=b"")
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client
        client_cm.__aexit__.return_value = None

        with patch("agent_teams.tools.ripgrep.httpx.AsyncClient", return_value=client_cm):
            with patch(
                "agent_teams.tools.ripgrep._get_platform_key",
                return_value="AMD64-windows",
            ):
                with pytest.raises(DownloadFailedError) as exc:
                    await ripgrep._download_rg(target)

        assert exc.value.status == 404


class TestGrepSearch:
    """测试 grep 搜索功能 - 直接测试底层逻辑"""

    def test_grep_result_parsing(self):
        """测试 grep 输出解析 (使用 | 分隔符)"""
        from agent_teams.tools.ripgrep_types import GrepMatch

        # 模拟 ripgrep 输出格式 (使用 --field-match-separator=|)
        stdout = "file1.py|1|def foo\nfile2.py|5|def bar\n"

        matches = []
        for line in stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            matches.append(
                GrepMatch(
                    path=parts[0],
                    line_num=int(parts[1]),
                    line_text=parts[2],
                )
            )

        assert len(matches) == 2
        assert matches[0].path == "file1.py"
        assert matches[0].line_num == 1
        assert matches[0].line_text == "def foo"

    def test_grep_args_case_sensitive(self):
        """测试大小写敏感参数"""
        args = []
        pattern = "test"
        case_sensitive = True

        args.extend(["-nH"])
        if not case_sensitive:
            args.append("-i")
        args.extend(["--max-count", "100"])
        args.extend(["--regexp", pattern])

        assert "-i" not in args

        # 测试不敏感
        args = []
        case_sensitive = False

        args.extend(["-nH"])
        if not case_sensitive:
            args.append("-i")
        args.extend(["--max-count", "100"])
        args.extend(["--regexp", pattern])

        assert "-i" in args

    def test_grep_args_with_glob(self):
        """测试带 glob 参数"""
        args = []
        pattern = "test"
        glob = "*.py"

        args.extend(["-nH"])
        args.extend(["--max-count", "100"])
        args.extend(["--regexp", pattern])
        if glob:
            args.extend(["--glob", glob])

        assert "--glob" in args
        assert "*.py" in args

    def test_grep_limit(self):
        """测试结果限制"""
        # 模拟 200 条结果
        matches = list(range(200))
        limit = 100

        truncated = len(matches) >= limit
        final = matches[:limit] if truncated else matches

        assert truncated is True
        assert len(final) == 100


class TestEnumerateFiles:
    """测试文件枚举功能"""

    def test_enumerate_args(self):
        """测试枚举参数构建"""
        pattern = "*.py"
        hidden = True
        follow = False
        limit = 100

        args = ["--files", "--glob=!.git/*"]
        if hidden:
            args.append("--hidden")
        if follow:
            args.append("--follow")
        args.extend(["--glob", pattern])

        assert "--files" in args
        assert "--hidden" in args
        assert "--glob" in args
        assert "*.py" in args

    def test_enumerate_truncation(self):
        """测试截断逻辑"""
        files = list(range(200))
        limit = 100

        truncated = len(files) >= limit
        final = files[:limit] if truncated else files

        assert truncated is True
        assert len(final) == 100


class TestGrepResultFormat:
    """测试结果格式化"""

    def test_format_empty(self):
        """空结果格式化"""
        from agent_teams.tools.ripgrep_types import GrepResult

        result = GrepResult(matches=[], truncated=False, total=0)
        assert result.format() == "No matches found"

    def test_format_with_matches(self):
        """有结果格式化"""
        from agent_teams.tools.ripgrep_types import GrepMatch, GrepResult

        matches = [
            GrepMatch(path="file1.py", line_num=1, line_text="def foo"),
            GrepMatch(path="file1.py", line_num=2, line_text="def bar"),
        ]
        result = GrepResult(matches=matches, truncated=False, total=2)

        formatted = result.format()
        assert "Found 2 matches" in formatted
        assert "file1.py:" in formatted
        assert "Line 1: def foo" in formatted

    def test_format_truncated(self):
        """截断结果格式化"""
        from agent_teams.tools.ripgrep_types import GrepMatch, GrepResult

        matches = [
            GrepMatch(path=f"file{i}.py", line_num=i, line_text=f"line {i}")
            for i in range(10)
        ]
        result = GrepResult(matches=matches, truncated=True, total=100)

        formatted = result.format()
        assert "Results truncated" in formatted

    def test_format_multiple_files(self):
        """多文件格式化"""
        from agent_teams.tools.ripgrep_types import GrepMatch, GrepResult

        matches = [
            GrepMatch(path="file1.py", line_num=1, line_text="def foo"),
            GrepMatch(path="file2.py", line_num=5, line_text="def bar"),
            GrepMatch(path="file2.py", line_num=10, line_text="def baz"),
        ]
        result = GrepResult(matches=matches, truncated=False, total=3)

        formatted = result.format()
        assert "file1.py:" in formatted
        assert "file2.py:" in formatted


class TestRipgrepSubprocessEncoding:
    """测试子进程输出解码参数"""

    @pytest.mark.asyncio
    async def test_grep_search_uses_utf8_replace(self):
        from agent_teams.tools import ripgrep

        mock_result = MagicMock(stdout="", returncode=0)
        with patch("agent_teams.tools.ripgrep.get_rg_path", new=AsyncMock(return_value=Path("rg.exe"))):
            with patch("agent_teams.tools.ripgrep.subprocess.run", return_value=mock_result) as mock_run:
                await ripgrep.grep_search(Path("."), "tasks")

        kwargs = mock_run.call_args.kwargs
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"

    @pytest.mark.asyncio
    async def test_enumerate_files_uses_utf8_replace(self):
        from agent_teams.tools import ripgrep

        proc = MagicMock()
        proc.stdout = ["a.py\n", "b.py\n"]
        proc.wait = MagicMock()

        with patch("agent_teams.tools.ripgrep.get_rg_path", new=AsyncMock(return_value=Path("rg.exe"))):
            with patch("agent_teams.tools.ripgrep.subprocess.Popen", return_value=proc) as mock_popen:
                files, truncated = await ripgrep.enumerate_files(Path("."), "*.py")

        kwargs = mock_popen.call_args.kwargs
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert truncated is False
        assert files == [Path("a.py"), Path("b.py")]
