from pathlib import Path

import pytest

from mini_claude_code.tools.filesystem import run_glob, run_read, run_write, safe_path


def test_write_read_and_glob_round_trip(tmp_path: Path) -> None:
    result = run_write("nested/example.txt", "hello\nworld", base_dir=tmp_path)

    assert result == "成功写入文件：nested/example.txt"
    assert run_read("nested/example.txt", base_dir=tmp_path).endswith("\nhello\nworld")
    assert run_read("nested/example.txt", limit=1, base_dir=tmp_path).endswith("\nhello")
    assert run_glob("**/*.txt", base_dir=tmp_path) == str(Path("nested/example.txt"))


def test_safe_path_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不允许访问当前工作目录之外的文件"):
        safe_path("../secret.txt", base_dir=tmp_path)


def test_glob_rejects_parent_pattern(tmp_path: Path) -> None:
    assert run_glob("../*.txt", base_dir=tmp_path) == "错误：不允许查找工作目录之外的文件"
