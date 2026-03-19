from __future__ import annotations

import pytest


class TestIsBinaryFile:
    def test_binary_extension_zip(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"PK\x03\x04")

        assert is_binary_file(test_file, 4) is True

    def test_binary_extension_exe(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"MZ")

        assert is_binary_file(test_file, 2) is True

    def test_text_file(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('world')")

        assert is_binary_file(test_file, test_file.stat().st_size) is False

    def test_null_byte(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello\x00world")

        assert is_binary_file(test_file, 11) is True

    def test_empty_file(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "empty.txt"
        test_file.touch()

        assert is_binary_file(test_file, 0) is False


class TestReadFileContent:
    @pytest.mark.asyncio
    async def test_read_file_all_lines(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\nline3\n")

        lines, total, truncated_lines, truncated_bytes = await read_file_content(
            test_file, offset=1, limit=10
        )

        assert lines == ["line1", "line2", "line3"]
        assert total == 3
        assert truncated_lines is False
        assert truncated_bytes is False

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        lines, total, _, _ = await read_file_content(test_file, offset=3, limit=2)

        assert lines == ["line3", "line4"]
        assert total == 5

    @pytest.mark.asyncio
    async def test_read_line_limit(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("\n".join([f"line{i}" for i in range(20)]))

        lines, total, truncated, _ = await read_file_content(
            test_file, offset=1, limit=5
        )

        assert len(lines) == 5
        assert truncated is True

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "empty.txt"
        test_file.touch()

        lines, total, _, _ = await read_file_content(test_file)

        assert lines == []
        assert total == 0


class TestReadDirectory:
    def test_read_directory(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "dir1").mkdir()
        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.py").touch()

        entries, total, truncated = read_directory(tmp_path, offset=1, limit=10)

        assert "dir1/" in entries
        assert "file1.txt" in entries
        assert "file2.py" in entries
        assert total == 3
        assert truncated is False

    def test_read_directory_with_offset(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()
        (tmp_path / "c.txt").touch()

        entries, total, truncated = read_directory(tmp_path, offset=2, limit=1)

        assert len(entries) == 1
        assert entries[0] == "b.txt"
        assert total == 3
        assert truncated is True

    def test_read_directory_sorted(self, tmp_path):
        from agent_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "z.txt").touch()
        (tmp_path / "a.txt").touch()
        (tmp_path / "m.txt").touch()

        entries, _, _ = read_directory(tmp_path, offset=1, limit=10)

        assert entries[0] == "a.txt"
        assert entries[1] == "m.txt"
        assert entries[2] == "z.txt"


def test_project_read_result_keeps_output_first_shape() -> None:
    from agent_teams.tools.workspace_tools.read import _project_read_result

    projected = _project_read_result(
        output="<content>\n1: hello\n</content>",
        truncated=True,
        next_offset=2,
    )

    assert projected.visible_data == {
        "output": "<content>\n1: hello\n</content>",
        "truncated": True,
        "next_offset": 2,
    }
    assert projected.internal_data == {
        "output": "<content>\n1: hello\n</content>",
        "truncated": True,
        "next_offset": 2,
    }
