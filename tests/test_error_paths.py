import pytest

from modify_code import resolve, parse_bool, is_valid_dotted


def test_parse_bool_invalid():
    with pytest.raises(ValueError):
        parse_bool("maybe")


@pytest.mark.parametrize(
    "name,valid",
    [
        ("X", True),
        ("X.y", True),
        ("x_y.z1", True),
        ("1bad", False),
        ("bad.", False),
        ("bad..name", False),
    ],
)
def test_is_valid_dotted(name, valid):
    assert is_valid_dotted(name) is valid


def test_resolve_unknown_command():
    with pytest.raises(ValueError):
        resolve("no_such_command", ["x"])
