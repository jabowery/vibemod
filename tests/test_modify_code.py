import pytest
import tempfile
import os
import shutil
from pathlib import Path
from vibemod.modify_code import (
    normalize_llm_quirks,
    extract_command_blocks,
    canonicalize_command,
    execute_canonical,
    parse_bool,
    CommandBlock,
    apply_modspec,
)


# ============================================================================
# PHASE 1 TESTS: Extract Command Blocks
# ============================================================================

class TestNormalizeLLMQuirks:
    """Test the LLM output normalization."""
    
    def test_normalize_mmm_header_with_extra_spaces(self):
        """Test that MMM headers with extra spaces are normalized."""
        input_text = "MMM   create_file   MMM  \n"
        result = normalize_llm_quirks(input_text)
        assert result == "MMM create_file MMM\n"
    
    def test_normalize_mmm_header_with_trailing_text(self):
        """Test that MMM headers with trailing text are normalized."""
        input_text = "MMM declare MMM some extra text\n"
        result = normalize_llm_quirks(input_text)
        assert result == "MMM declare MMM\n"
    
    def test_normalize_separator_with_spaces(self):
        """Test that separator lines with spaces are normalized."""
        input_text = "  @@@@@@  \n"
        result = normalize_llm_quirks(input_text)
        assert result == "@@@@@@\n"
    
    def test_escaped_separator(self):
        """Test that escaped separators are preserved."""
        input_text = "some text \\@@@@@@ more text\n"
        result = normalize_llm_quirks(input_text)
        assert "\\@@@@@@" in result
    
    def test_preserves_normal_lines(self):
        """Test that normal lines pass through unchanged."""
        input_text = "def foo():\n    pass\n"
        result = normalize_llm_quirks(input_text)
        assert result == input_text


class TestExtractCommandBlocks:
    """Test command block extraction."""
    
    def test_single_command_no_arguments(self):
        """Test extraction of a command with no arguments."""
        input_text = "MMM make_directory MMM\nsome/path\n"
        blocks = extract_command_blocks(input_text)
        assert len(blocks) == 1
        assert blocks[0].command == "make_directory"
        assert blocks[0].arguments == ["some/path\n"]
    
    def test_single_command_multiple_arguments(self):
        """Test extraction with multiple arguments separated by @@@@@@."""
        input_text = """MMM create_file MMM
test.py
@@@@@@
print("hello")
@@@@@@
True
"""
        blocks = extract_command_blocks(input_text)
        assert len(blocks) == 1
        assert blocks[0].command == "create_file"
        assert len(blocks[0].arguments) == 3
        assert blocks[0].arguments[0].strip() == "test.py"
        assert 'print("hello")' in blocks[0].arguments[1]
        assert blocks[0].arguments[2].strip() == "True"
    
    def test_multiple_commands(self):
        """Test extraction of multiple command blocks."""
        input_text = """MMM modification_description MMM
First mod
MMM create_file MMM
file1.py
@@@@@@
content1
MMM make_directory MMM
dir1
"""
        blocks = extract_command_blocks(input_text)
        assert len(blocks) == 3
        assert blocks[0].command == "modification_description"
        assert blocks[1].command == "create_file"
        assert blocks[2].command == "make_directory"
    
    def test_escaped_separator_in_content(self):
        """Test that escaped separators in content are unescaped."""
        input_text = """MMM create_file MMM
test.py
@@@@@@
text \\@@@@@@ more text
"""
        blocks = extract_command_blocks(input_text)
        assert "@@@@@@" in blocks[0].arguments[1]
        assert "\\@@@@@@" not in blocks[0].arguments[1]
    
    def test_empty_sections(self):
        """Test handling of empty sections."""
        input_text = """MMM create_file MMM
file.py
@@@@@@
@@@@@@
False
"""
        blocks = extract_command_blocks(input_text)
        assert len(blocks[0].arguments) == 3
        assert blocks[0].arguments[1] == ""


# ============================================================================
# PHASE 2 TESTS: Canonicalize Commands
# ============================================================================

class TestParseBool:
    """Test boolean parsing."""
    
    @pytest.mark.parametrize("input_str,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("y", True),
        ("1", True),
        ("", True),  # Empty defaults to True
        ("false", False),
        ("False", False),
        ("no", False),
        ("n", False),
        ("0", False),
    ])
    def test_valid_booleans(self, input_str, expected):
        """Test parsing of valid boolean strings."""
        assert parse_bool(input_str) == expected
    
    def test_invalid_boolean_raises(self):
        """Test that invalid boolean strings raise ValueError."""
        with pytest.raises(ValueError, match="Invalid boolean"):
            parse_bool("maybe")


class TestCanonicalizeCommand:
    """Test command canonicalization."""
    
    def test_modification_description(self):
        """Test canonicalization of modification_description."""
        block = CommandBlock("modification_description", ["Fix the bug\n"])
        cmd, args = canonicalize_command(block)
        assert cmd == "modification_description"
        assert args == ["Fix the bug\n"]
    
    def test_modification_description_wrong_arity(self):
        """Test that wrong arity raises error."""
        block = CommandBlock("modification_description", ["arg1", "arg2"])
        with pytest.raises(ValueError, match="requires exactly 1 argument"):
            canonicalize_command(block)
    
    def test_create_file_minimal(self):
        """Test create_file with minimal arguments."""
        block = CommandBlock("create_file", ["test.py", "print('hi')"])
        cmd, args = canonicalize_command(block)
        assert cmd == "create_file"
        assert args == ["test.py", "print('hi')", False]
    
    def test_create_file_with_executable(self):
        """Test create_file with make_executable flag."""
        block = CommandBlock("create_file", ["script.sh", "#!/bin/bash\n", "true"])
        cmd, args = canonicalize_command(block)
        assert cmd == "create_file"
        assert args == ["script.sh", "#!/bin/bash\n", True]
    
    def test_replace_file_contents(self):
        """Test replace_file_contents canonicalization."""
        block = CommandBlock("replace_file_contents", ["file.py", "new content"])
        cmd, args = canonicalize_command(block)
        assert cmd == "replace_file_contents"
        assert args == ["file.py", "new content", False]
    
    def test_move_file(self):
        """Test move_file canonicalization."""
        block = CommandBlock("move_file", ["src.py", "dst.py"])
        cmd, args = canonicalize_command(block)
        assert cmd == "move_file"
        assert args == ["src.py", "dst.py"]
    
    def test_make_directory(self):
        """Test make_directory canonicalization."""
        block = CommandBlock("make_directory", ["my_dir"])
        cmd, args = canonicalize_command(block)
        assert cmd == "make_directory"
        assert args == ["my_dir"]
    
    def test_remove_file_minimal(self):
        """Test remove_file with minimal arguments."""
        block = CommandBlock("remove_file", ["old.py"])
        cmd, args = canonicalize_command(block)
        assert cmd == "remove_file"
        assert args == ["old.py", False]
    
    def test_remove_file_recursive(self):
        """Test remove_file with recursive flag."""
        block = CommandBlock("remove_file", ["old_dir", "true"])
        cmd, args = canonicalize_command(block)
        assert cmd == "remove_file"
        assert args == ["old_dir", True]
    
    def test_update_header(self):
        """Test update_header canonicalization."""
        block = CommandBlock("update_header", ["module.py", "# New header\n"])
        cmd, args = canonicalize_command(block)
        assert cmd == "update_header"
        assert args == ["module.py", "# New header\n"]
    
    def test_declare(self):
        """Test declare canonicalization."""
        block = CommandBlock("declare", ["app.py", "MyClass.method", "def method(self): pass"])
        cmd, args = canonicalize_command(block)
        assert cmd == "declare"
        assert args == ["app.py", "MyClass.method", "def method(self): pass"]
    
    def test_update_declaration(self):
        """Test update_declaration canonicalization."""
        block = CommandBlock("update_declaration", ["app.py", "func", "def func(): return 42"])
        cmd, args = canonicalize_command(block)
        assert cmd == "update_declaration"
        assert args == ["app.py", "func", "def func(): return 42"]
    
    def test_remove_declaration(self):
        """Test remove_declaration canonicalization."""
        block = CommandBlock("remove_declaration", ["app.py", "old_func"])
        cmd, args = canonicalize_command(block)
        assert cmd == "remove_declaration"
        assert args == ["app.py", "old_func"]
    
    def test_unknown_command_raises(self):
        """Test that unknown commands raise ValueError."""
        block = CommandBlock("unknown_command", ["arg"])
        with pytest.raises(ValueError, match="Unknown command"):
            canonicalize_command(block)


# ============================================================================
# PHASE 3 TESTS: Execute Canonical Commands
# ============================================================================

class TestExecuteCanonical:
    """Test command execution."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir)
    
    def test_modification_description_noop(self):
        """Test that modification_description is a no-op."""
        execute_canonical("modification_description", ["some description"])
        # Should not raise any errors
    
    def test_create_file(self, temp_dir):
        """Test file creation."""
        file_path = os.path.join(temp_dir, "test.py")
        content = "print('hello')\n"
        execute_canonical("create_file", [file_path, content, False])
        
        assert os.path.exists(file_path)
        with open(file_path) as f:
            assert f.read() == content
    
    def test_create_file_executable(self, temp_dir):
        """Test creating an executable file (non-Windows)."""
        if os.name == 'nt':
            pytest.skip("Executable test not applicable on Windows")
        
        file_path = os.path.join(temp_dir, "script.sh")
        content = "#!/bin/bash\necho 'hi'\n"
        execute_canonical("create_file", [file_path, content, True])
        
        assert os.path.exists(file_path)
        # Check if executable bit is set
        assert os.access(file_path, os.X_OK)
    
    def test_replace_file_contents(self, temp_dir):
        """Test replacing file contents."""
        file_path = os.path.join(temp_dir, "existing.py")
        
        # Create initial file
        with open(file_path, 'w') as f:
            f.write("old content")
        
        # Replace contents
        new_content = "new content\n"
        execute_canonical("replace_file_contents", [file_path, new_content, False])
        
        with open(file_path) as f:
            assert f.read() == new_content
    
    def test_make_directory(self, temp_dir):
        """Test directory creation."""
        dir_path = os.path.join(temp_dir, "new_dir")
        execute_canonical("make_directory", [dir_path])
        
        assert os.path.isdir(dir_path)
    
    def test_make_directory_nested(self, temp_dir):
        """Test nested directory creation."""
        dir_path = os.path.join(temp_dir, "level1", "level2", "level3")
        execute_canonical("make_directory", [dir_path])
        
        assert os.path.isdir(dir_path)
    
    def test_make_directory_exists_ok(self, temp_dir):
        """Test that make_directory doesn't fail if directory exists."""
        dir_path = os.path.join(temp_dir, "existing_dir")
        os.makedirs(dir_path)
        
        # Should not raise error
        execute_canonical("make_directory", [dir_path])
        assert os.path.isdir(dir_path)
    
    def test_remove_file(self, temp_dir):
        """Test file removal."""
        file_path = os.path.join(temp_dir, "to_remove.py")
        with open(file_path, 'w') as f:
            f.write("content")
        
        execute_canonical("remove_file", [file_path, False])
        assert not os.path.exists(file_path)
    
    def test_remove_directory_non_recursive(self, temp_dir):
        """Test removing empty directory."""
        dir_path = os.path.join(temp_dir, "empty_dir")
        os.makedirs(dir_path)
        
        execute_canonical("remove_file", [dir_path, False])
        assert not os.path.exists(dir_path)
    
    def test_remove_directory_recursive(self, temp_dir):
        """Test recursive directory removal."""
        dir_path = os.path.join(temp_dir, "dir_with_files")
        os.makedirs(dir_path)
        
        # Create some files in the directory
        with open(os.path.join(dir_path, "file1.txt"), 'w') as f:
            f.write("content1")
        with open(os.path.join(dir_path, "file2.txt"), 'w') as f:
            f.write("content2")
        
        execute_canonical("remove_file", [dir_path, True])
        assert not os.path.exists(dir_path)
    
    def test_move_file(self, temp_dir):
        """Test file moving."""
        src_path = os.path.join(temp_dir, "source.py")
        dst_path = os.path.join(temp_dir, "destination.py")
        
        with open(src_path, 'w') as f:
            f.write("content")
        
        execute_canonical("move_file", [src_path, dst_path])
        
        assert not os.path.exists(src_path)
        assert os.path.exists(dst_path)
        with open(dst_path) as f:
            assert f.read() == "content"
    
    def test_update_header_simple(self, temp_dir):
        """Test updating module header."""
        file_path = os.path.join(temp_dir, "module.py")
        
        # Create file with old header
        original = """# Old header
import os

def func():
    pass

class MyClass:
    pass
"""
        with open(file_path, 'w') as f:
            f.write(original)
        
        new_header = "# New header\nimport sys"
        execute_canonical("update_header", [file_path, new_header])
        
        with open(file_path) as f:
            content = f.read()
        
        assert "# New header" in content
        assert "import sys" in content
        assert "def func():" in content
        assert "class MyClass:" in content
    
    def test_declare_function(self, temp_dir):
        """Test declaring a new function."""
        file_path = os.path.join(temp_dir, "test_module.py")
        
        # Create initial file
        with open(file_path, 'w') as f:
            f.write("# Module\n\ndef existing():\n    pass\n")
        
        new_func = """
def new_function(x):
    return x * 2
"""
        execute_canonical("declare", [file_path, "new_function", new_func])
        
        with open(file_path) as f:
            content = f.read()
        
        assert "def new_function(x):" in content
        assert "return x * 2" in content
        assert "def existing():" in content
    
    def test_declare_replaces_existing(self, temp_dir):
        """Test that declare replaces existing function."""
        file_path = os.path.join(temp_dir, "test_module.py")
        
        # Create file with function to replace
        with open(file_path, 'w') as f:
            f.write("def func():\n    return 1\n")
        
        new_func = "def func():\n    return 2\n"
        execute_canonical("declare", [file_path, "func", new_func])
        
        with open(file_path) as f:
            content = f.read()
        
        # Should only appear once with new implementation
        assert content.count("def func():") == 1
        assert "return 2" in content
        assert "return 1" not in content
    
    def test_declare_nested_method(self, temp_dir):
        """Test declaring a method inside a class."""
        file_path = os.path.join(temp_dir, "test_class.py")
        
        # Create file with class
        with open(file_path, 'w') as f:
            f.write("""
class MyClass:
    def existing_method(self):
        pass
""")
        
        new_method = """
def new_method(self, x):
    return x + 1
"""
        execute_canonical("declare", [file_path, "MyClass.new_method", new_method])
        
        with open(file_path) as f:
            content = f.read()
        
        assert "class MyClass:" in content
        assert "def new_method(self, x):" in content
        assert "def existing_method(self):" in content
    
    def test_remove_declaration(self, temp_dir):
        """Test removing a function declaration."""
        file_path = os.path.join(temp_dir, "test_module.py")
        
        # Create file with multiple functions
        with open(file_path, 'w') as f:
            f.write("""
def keep_this():
    pass

def remove_this():
    pass

def also_keep():
    pass
""")
        
        execute_canonical("remove_declaration", [file_path, "remove_this"])
        
        with open(file_path) as f:
            content = f.read()
        
        assert "def keep_this():" in content
        assert "def also_keep():" in content
        assert "remove_this" not in content
    
    def test_declare_with_imports(self, temp_dir):
        """Test that declare properly handles imports in new code."""
        file_path = os.path.join(temp_dir, "test_module.py")
        
        with open(file_path, 'w') as f:
            f.write("# Module\n")
        
        new_func = """
import json
from typing import List

def parse_json(data: str) -> List:
    return json.loads(data)
"""
        execute_canonical("declare", [file_path, "parse_json", new_func])
        
        with open(file_path) as f:
            content = f.read()
        
        # Imports should be at module level
        lines = content.split('\n')
        import_indices = [i for i, line in enumerate(lines) if 'import' in line]
        func_index = next(i for i, line in enumerate(lines) if 'def parse_json' in line)
        
        # All imports should come before the function
        assert all(i < func_index for i in import_indices)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """End-to-end integration tests."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir)
    
    def test_full_pipeline_simple(self, temp_dir):
        """Test complete pipeline from spec to execution."""
        spec_content = """MMM modification_description MMM
Create a simple Python script

MMM create_file MMM
test.py
@@@@@@
print("Hello, World!")
"""
        
        spec_path = os.path.join(temp_dir, "spec.modspec")
        with open(spec_path, 'w') as f:
            f.write(spec_content)
        
        # Phase 1
        blocks = extract_command_blocks(spec_content)
        assert len(blocks) == 2
        
        # Phase 2
        canonical = []
        for block in blocks:
            cmd, args = canonicalize_command(block)
            canonical.append((cmd, args))
        
        # Phase 3
        os.chdir(temp_dir)
        for cmd, args in canonical:
            execute_canonical(cmd, args)
        
        # Verify results
        test_file = os.path.join(temp_dir, "test.py")
        assert os.path.exists(test_file)
        with open(test_file) as f:
            assert 'print("Hello, World!")' in f.read()
    
    def test_full_pipeline_complex(self, temp_dir):
        """Test complex modification with multiple operations."""
        spec_content = """MMM modification_description MMM
Create a Python module with class and tests

MMM make_directory MMM
src

MMM create_file MMM
src/calculator.py
@@@@@@
class Calculator:
    def add(self, a, b):
        return a + b
    
    def subtract(self, a, b):
        return a - b
@@@@@@
False

MMM make_directory MMM
tests

MMM create_file MMM
tests/test_calculator.py
@@@@@@
from src.calculator import Calculator

def test_add():
    calc = Calculator()
    assert calc.add(2, 3) == 5
"""
        
        # Extract and execute
        blocks = extract_command_blocks(spec_content)
        os.chdir(temp_dir)
        
        for block in blocks:
            cmd, args = canonicalize_command(block)
            execute_canonical(cmd, args)
        
        # Verify directory structure
        assert os.path.isdir(os.path.join(temp_dir, "src"))
        assert os.path.isdir(os.path.join(temp_dir, "tests"))
        assert os.path.exists(os.path.join(temp_dir, "src", "calculator.py"))
        assert os.path.exists(os.path.join(temp_dir, "tests", "test_calculator.py"))
        
        # Verify content
        with open(os.path.join(temp_dir, "src", "calculator.py")) as f:
            content = f.read()
            assert "class Calculator:" in content
            assert "def add(self, a, b):" in content


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_malformed_command_ignored(self):
        """Test that lines without MMM headers are ignored."""
        input_text = """Some random text
MMM create_file MMM
test.py
@@@@@@
content
More random text
"""
        blocks = extract_command_blocks(input_text)
        assert len(blocks) == 1
        assert blocks[0].command == "create_file"
    
    def test_empty_input(self):
        """Test handling of empty input."""
        blocks = extract_command_blocks("")
        assert blocks == []
    
    def test_command_with_no_body(self):
        """Test command with no arguments."""
        input_text = "MMM make_directory MMM\n"
        blocks = extract_command_blocks(input_text)
        assert len(blocks) == 1
        assert blocks[0].arguments == []
    
    def test_execute_nonexistent_file_raises(self):
        """Test that operations on nonexistent files raise appropriate errors."""
        with pytest.raises(FileNotFoundError):
            execute_canonical("update_header", ["/nonexistent/file.py", "# header"])
    
    def test_declare_invalid_python_raises(self):
        """Test that invalid Python syntax in declare raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.py")
            with open(file_path, 'w') as f:
                f.write("# Empty module\n")
            
            invalid_code = "def func(\n    # Missing closing paren"
            
            with pytest.raises(ValueError, match="Invalid content syntax"):
                execute_canonical("declare", [file_path, "func", invalid_code])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
