import pytest

from modify_code import resolve


def test_resolve_modification_description():
    args = resolve("modification_description", ["some description"])
    assert args == ["some description"]


def test_resolve_create_file_2_arity():
    args = resolve("create_file", ["a.txt", "content"])
    assert args[0] == "a.txt"
    assert args[1] == "content"
    assert args[2] is False  # default exec flag


def test_resolve_create_file_3_arity_true():
    args = resolve("create_file", ["a.txt", "content", "true"])
    assert args == ["a.txt", "content", True]


def test_resolve_create_file_bad_arity():
    with pytest.raises(ValueError):
        resolve("create_file", ["only-one-section"])


def test_resolve_declare_shorthand_ok():
    # Schema A: "<file>.py.<dotted>"
    args = resolve("declare", ["pkg/mod.py.MyClass", "class MyClass:\n    pass\n"])
    assert args[0] == "pkg/mod.py"
    assert args[1] == "MyClass"
    assert "class MyClass" in args[2]


def test_resolve_declare_explicit_ok():
    args = resolve(
        "declare",
        ["pkg/mod.py", "A.B", "def B():\n    return 1\n"],
    )
    assert args[0] == "pkg/mod.py"
    assert args[1] == "A.B"
    assert "def B" in args[2]


def test_resolve_declare_invalid_shorthand():
    with pytest.raises(ValueError):
        resolve("declare", ["not-a-py.sh.MyFunc", "def MyFunc(): pass"])


def test_resolve_declare_empty_body():
    with pytest.raises(ValueError):
        resolve("declare", ["x.py.Foo", "   "])
