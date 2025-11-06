import textwrap

from modify_code import parse


def test_parse_single_block_no_sections():
    spec = "MMM modification_description MMM\nthis is a change\n"
    blocks = parse(spec)
    assert len(blocks) == 1
    assert blocks[0][0] == "modification_description"
    assert blocks[0][1] == ["this is a change\n"]


def test_parse_multiple_sections_and_escape():
    # second section contains an escaped @@@@@@
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
    assert sections[0].strip() == "path/to/file.txt"
    # escaped sequence restored
    assert "\\@@@@@@" not in sections[1]
    assert "@@@@@@" in sections[1]
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
