import ast
import textwrap
from pathlib import Path

from vibemod.modify_code import modify_declaration


def test_modify_declaration_inserts_new_function(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def existing():\n    return 1\n", encoding="utf-8")

    # add new function at module scope
    modify_declaration(str(f), "newfunc", "def newfunc():\n    return 2\n", remove=False)

    mod = ast.parse(f.read_text(encoding="utf-8"))
    names = [n.name for n in mod.body if isinstance(n, ast.FunctionDef)]
    assert "existing" in names
    assert "newfunc" in names


def test_modify_declaration_replaces_existing(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        textwrap.dedent(
            """
            def target():
                return 1
            """
        ),
        encoding="utf-8",
    )

    modify_declaration(
        str(f),
        "target",
        "def target():\n    return 999\n",
        remove=False,
    )

    mod = ast.parse(f.read_text(encoding="utf-8"))
    fn = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "target"][0]
    # ensure it's the replaced body
    assert ast.get_source_segment(f.read_text(encoding="utf-8"), fn).strip().startswith("def target()")
    # quick check: return 999 is inside
    assert "999" in f.read_text(encoding="utf-8")


def test_modify_declaration_nested_class_scope(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        textwrap.dedent(
            """
            class Outer:
                def existing(self):
                    return 1
            """
        ),
        encoding="utf-8",
    )

    # target Outer.newmethod
    modify_declaration(
        str(f),
        "Outer.newmethod",
        "def newmethod(self):\n    return 5\n",
        remove=False,
    )

    mod = ast.parse(f.read_text(encoding="utf-8"))
    outer = [n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "Outer"][0]
    names = [n.name for n in outer.body if isinstance(n, ast.FunctionDef)]
    assert "newmethod" in names


def test_modify_declaration_remove(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(
        textwrap.dedent(
            """
            def keep():
                return 1

            def drop():
                return 2
            """
        ),
        encoding="utf-8",
    )

    modify_declaration(str(f), "drop", None, remove=True)
    mod = ast.parse(f.read_text(encoding="utf-8"))
    names = [n.name for n in mod.body if isinstance(n, ast.FunctionDef)]
    assert "keep" in names
    assert "drop" not in names
