# tests/test_parse_modspec.py
import textwrap

import pytest

from modify_code import parse, resolve


# ───────────────────────────── parse-level tests ─────────────────────────────

def test_parse_single_block_no_sections():
    spec = "MMM modification_description MMM\nthis is a change\n"
    blocks = parse(spec)
    assert len(blocks) == 1
    assert blocks[0][0] == "modification_description"
    assert blocks[0][1] == ["this is a change\n"]


def test_parse_multiple_sections_and_escape():
    """
    The second section contains a literal "@@@@@@" that is written in the
    spec as an escaped sequence "\@@@@@@" and must be unescaped by the parser.
    """
    spec = textwrap.dedent(
        r"""
        MMM create_file MMM
        path/to/file.txt
        @@@@@@
        line 1
        \@@@@@@ <- literal
        line 2
        @@@@@@
        true
        """
    ).lstrip()
    blocks = parse(spec)
    assert len(blocks) == 1
    cmd, sections = blocks[0]
    assert cmd == "create_file"
    assert len(sections) == 3

    # section 0: path
    assert sections[0].strip() == "path/to/file.txt"

    # section 1: body, should contain the literal "@@@@@@" and NOT the backslash form
    body = sections[1]
    assert "line 1" in body
    # escape should have been resolved
    assert r"\@@@@@@" not in body
    assert "@@@@@@ <- literal" in body
    assert "line 2" in body

    # section 2: exec flag
    assert sections[2].strip() == "true"

def test_parse_two_blocks_back_to_back():
    spec = textwrap.dedent(
        """
        MMM modification_description MMM
        do stuff
        MMM make_directory MMM
        mydir
        """
    ).lstrip()
    blocks = parse(spec)
    assert len(blocks) == 2
    assert blocks[0][0] == "modification_description"
    assert blocks[1][0] == "make_directory"


# ───────────────────────────── resolve-level tests ─────────────────────────────

def test_resolve_modification_description_ok():
    sections = ["this is a description\n"]
    args = resolve("modification_description", sections)
    assert args == ["this is a description\n"]


def test_resolve_modification_description_wrong_arity():
    with pytest.raises(ValueError):
        resolve("modification_description", ["too", "many"])


def test_resolve_create_file_with_two_sections_defaults_exec_false():
    sections = ["path/to/file.txt\n", "file contents\n"]
    path, content, make_exec = resolve("create_file", sections)
    assert path == "path/to/file.txt"
    assert content == "file contents\n"
    assert make_exec is False


def test_resolve_create_file_with_true_flag():
    sections = ["script.sh\n", "echo hi\n", "TrUe\n"]
    path, content, make_exec = resolve("create_file", sections)
    assert path == "script.sh"
    assert content == "echo hi\n"
    assert make_exec is True  # case-insensitive


def test_resolve_create_file_with_false_flag():
    sections = ["script.sh\n", "echo hi\n", "false\n"]
    path, content, make_exec = resolve("create_file", sections)
    assert path == "script.sh"
    assert content == "echo hi\n"
    assert make_exec is False


def test_resolve_create_file_invalid_arity_raises():
    # Too few sections
    with pytest.raises(ValueError):
        resolve("create_file", ["only_one_section"])
    # Too many sections
    with pytest.raises(ValueError):
        resolve("create_file", ["a", "b", "c", "d"])


def test_resolve_rejects_extra_empty_section_for_non_create():
    """
    With the strict resolver, non-create/replace commands do NOT silently
    trim a trailing empty section; they reject the wrong arity.
    """
    sections = ["mydir\n", "   "]
    with pytest.raises(ValueError):
        resolve("make_directory", sections)


def test_resolve_rejects_extra_false_section_for_non_create():
    """
    Similarly, a trailing 'false' does not get trimmed for non-create/replace
    commands; it is simply an extra (invalid) section.
    """
    sections = ["obsolete.txt\n", "false\n"]
    with pytest.raises(ValueError):
        resolve("remove_file", sections)

def test_resolve_move_file_ok():
    sections = ["src.txt\n", "dst.txt\n"]
    args = resolve("move_file", sections)
    assert args == ["src.txt", "dst.txt"]


def test_resolve_move_file_wrong_arity():
    with pytest.raises(ValueError):
        resolve("move_file", ["only_one"])


def test_resolve_make_directory_wrong_arity():
    with pytest.raises(ValueError):
        resolve("make_directory", ["dir1", "dir2"])


def test_resolve_update_header_ok():
    sections = ["path/to/file.py\n", "new header text\n"]
    args = resolve("update_header", sections)
    assert args == ["path/to/file.py", "new header text\n"]


def test_resolve_update_header_wrong_arity():
    with pytest.raises(ValueError):
        resolve("update_header", ["only_one_section"])


def test_resolve_declare_shorthand_ok():
    """
    Shorthand form: "<file.py>.<dotted_target>" + content.
    """
    sections = [
        "module.py.MyClass\n",
        "class MyClass:\n    pass\n",
    ]
    file_path, dotted_target, content = resolve("declare", sections)
    assert file_path == "module.py"
    assert dotted_target == "MyClass"
    assert "class MyClass" in content


def test_resolve_declare_shorthand_invalid_dotted_raises():
    sections = [
        "module.py.1notvalid\n",  # invalid identifier
        "class Something:\n    pass\n",
    ]
    with pytest.raises(ValueError):
        resolve("declare", sections)


def test_resolve_declare_full_form_ok():
    sections = [
        "module.py\n",
        "Outer.Inner\n",
        "class Inner:\n    pass\n",
    ]
    file_path, dotted_target, content = resolve("declare", sections)
    assert file_path == "module.py"
    assert dotted_target == "Outer.Inner"
    assert "class Inner" in content


def test_resolve_declare_requires_non_empty_content():
    sections = [
        "module.py\n",
        "MyClass\n",
        "   \n",
    ]
    with pytest.raises(ValueError):
        resolve("declare", sections)


def test_resolve_update_declaration_ok():
    sections = [
        "module.py\n",
        "Foo.bar\n",
        "def bar():\n    return 42\n",
    ]
    file_path, dotted_target, content = resolve("update_declaration", sections)
    assert file_path == "module.py"
    assert dotted_target == "Foo.bar"
    assert "def bar" in content


def test_resolve_update_declaration_wrong_arity():
    with pytest.raises(ValueError):
        resolve("update_declaration", ["a", "b"])  # needs 3


def test_resolve_update_declaration_invalid_dotted():
    sections = [
        "module.py\n",
        "not valid\n",
        "def f():\n    pass\n",
    ]
    with pytest.raises(ValueError):
        resolve("update_declaration", sections)


def test_resolve_remove_declaration_ok():
    sections = [
        "module.py\n",
        "MyClass.method\n",
    ]
    file_path, dotted_target = resolve("remove_declaration", sections)
    assert file_path == "module.py"
    assert dotted_target == "MyClass.method"


def test_resolve_remove_declaration_invalid_dotted():
    sections = [
        "module.py\n",
        "1notvalid\n",
    ]
    with pytest.raises(ValueError):
        resolve("remove_declaration", sections)


def test_resolve_unknown_command_raises():
    with pytest.raises(ValueError):
        resolve("nonexistent_command", ["x"])
