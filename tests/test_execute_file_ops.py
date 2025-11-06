import os
from pathlib import Path

from modify_code import execute


def test_execute_create_file(tmp_path):
    path = tmp_path / "a.txt"
    execute("create_file", [str(path), "hello", False])
    assert path.read_text(encoding="utf-8") == "hello"


def test_execute_make_directory(tmp_path):
    d = tmp_path / "adir"
    execute("make_directory", [str(d)])
    assert d.is_dir()


def test_execute_remove_file(tmp_path):
    f = tmp_path / "delme.txt"
    f.write_text("x", encoding="utf-8")
    execute("remove_file", [str(f)])
    assert not f.exists()


def test_execute_update_header(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        "def f():\n    return 1\n",
        encoding="utf-8",
    )
    execute("update_header", [str(f), "# new header\n"])
    content = f.read_text(encoding="utf-8")
    assert content.startswith("# new header\n")
    # function still there
    assert "def f" in content
