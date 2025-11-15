import ast
import textwrap
from pathlib import Path

from modify_code import modify_declaration


def test_modify_declaration_handles_indented_content(tmp_path):
    """
    Test that modify_declaration can handle content with leading indentation,
    such as method definitions copied from within a class body.
    """
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

    # Provide content with extra indentation
    indented_content = textwrap.dedent(
        """
            def newmethod(self):
                return 5
        """
    )[1:]  # Remove first newline, but actually to indent, we can add spaces
    # Better: simulate pasted indented code
    indented_content = "    def newmethod(self):\n        return 5\n"

    modify_declaration(
        str(f),
        "Outer.newmethod",
        indented_content,
        remove=False,
    )

    mod = ast.parse(f.read_text(encoding="utf-8"))
    outer = [n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "Outer"][0]
    names = [n.name for n in outer.body if isinstance(n, ast.FunctionDef)]
    assert "existing" in names
    assert "newmethod" in names

    # Check the source to ensure no extra indentation was preserved incorrectly
    source = f.read_text(encoding="utf-8")
    assert "    def existing(self):" in source  # class indent
    assert "        return 1" in source
    assert "    def newmethod(self):" in source  # same class indent
    assert "        return 5" in source