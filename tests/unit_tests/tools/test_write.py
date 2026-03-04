from __future__ import annotations

class TestAtomicWrite:
    """测试原子写入"""

    def test_atomic_write_creates_file(self, tmp_path):
        """测试原子写入创建文件"""
        from agent_teams.tools.workspace.write import atomic_write

        test_file = tmp_path / "new.txt"

        atomic_write(test_file, "hello world")

        assert test_file.exists()
        assert test_file.read_text() == "hello world"

    def test_atomic_write_overwrites(self, tmp_path):
        """测试原子写入覆盖"""
        from agent_teams.tools.workspace.write import atomic_write

        test_file = tmp_path / "test.txt"
        test_file.write_text("old content")

        atomic_write(test_file, "new content")

        assert test_file.read_text() == "new content"

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        """测试原子写入创建父目录"""
        from agent_teams.tools.workspace.write import atomic_write

        test_file = tmp_path / "subdir" / "nested" / "file.txt"

        atomic_write(test_file, "content")

        assert test_file.exists()
        assert test_file.read_text() == "content"

    def test_atomic_write_empty_content(self, tmp_path):
        """测试原子写入空内容"""
        from agent_teams.tools.workspace.write import atomic_write

        test_file = tmp_path / "empty.txt"

        atomic_write(test_file, "")

        assert test_file.exists()
        assert test_file.read_text() == ""

    def test_atomic_write_special_chars(self, tmp_path):
        """测试原子写入特殊字符"""
        from agent_teams.tools.workspace.write import atomic_write

        test_file = tmp_path / "special.txt"
        content = "line1\nline2\nline3 with 'quotes' and \"double quotes\""

        atomic_write(test_file, content)

        assert test_file.read_text() == content


class TestGenerateDiff:
    """测试 diff 生成"""

    def test_generate_diff_no_change(self):
        """测试无变化"""
        from agent_teams.tools.workspace.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nline2\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert diff == ""

    def test_generate_diff_modify(self):
        """测试修改"""
        from agent_teams.tools.workspace.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nmodified\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "---" in diff
        assert "+++" in diff
        assert "modified" in diff

    def test_generate_diff_add(self):
        """测试添加"""
        from agent_teams.tools.workspace.write import generate_diff

        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "+++" in diff
        assert "line3" in diff

    def test_generate_diff_delete(self):
        """测试删除"""
        from agent_teams.tools.workspace.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "---" in diff


class TestFormatDiffShort:
    """测试简短 diff"""

    def test_format_diff_no_changes(self):
        """测试无变化"""
        from agent_teams.tools.workspace.write import format_diff_short

        old = "line1\nline2\n"
        new = "line1\nline2\n"

        result = format_diff_short(old, new)

        assert result == "No changes"

    def test_format_diff_modify(self):
        """测试修改"""
        from agent_teams.tools.workspace.write import format_diff_short

        old = "line1\nline2\nline3\n"
        new = "line1\nmodified\nline3\n"

        result = format_diff_short(old, new)

        assert "~" in result
        assert "changed" in result

    def test_format_diff_add(self):
        """测试添加"""
        from agent_teams.tools.workspace.write import format_diff_short

        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"

        result = format_diff_short(old, new)

        assert "+" in result
        assert "added" in result

    def test_format_diff_delete(self):
        """测试删除"""
        from agent_teams.tools.workspace.write import format_diff_short

        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"

        result = format_diff_short(old, new)

        assert "-" in result
        assert "deleted" in result
