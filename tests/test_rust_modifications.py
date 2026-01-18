# tests/test_rust_modifications.py
"""
Test suite for Rust language support in VibeMod.

Requires: pip install tree-sitter tree-sitter-rust

These tests verify that the Rust handler correctly:
- Finds declarations (functions, structs, enums, impl blocks, traits)
- Handles nested declarations (methods within impl blocks)
- Updates file headers (use statements, extern crate, etc.)
- Integrates with the full VibeMod pipeline
"""

import os
import pytest
import tempfile
import textwrap
from pathlib import Path

# Skip all tests if tree-sitter-rust is not available
try:
    import tree_sitter_rust
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not TREE_SITTER_AVAILABLE,
    reason="tree-sitter and tree-sitter-rust not installed"
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def rust_handler():
    """Create a RustHandler instance."""
    from vibemod.handlers.rust_handler import RustHandler
    return RustHandler()


@pytest.fixture
def temp_rust_file(tmp_path):
    """Create a temporary Rust file for testing."""
    def _create(content: str, filename: str = "test.rs") -> Path:
        file_path = tmp_path / filename
        file_path.write_text(textwrap.dedent(content))
        return file_path
    return _create


@pytest.fixture
def sample_rust_source():
    """Sample Rust source code for testing."""
    return textwrap.dedent('''\
        use std::collections::HashMap;
        use std::io::{self, Read, Write};

        const MAX_SIZE: usize = 1024;

        fn helper_function() -> i32 {
            42
        }

        struct DataProcessor {
            data: Vec<u8>,
            cache: HashMap<String, String>,
        }

        impl DataProcessor {
            fn new() -> Self {
                DataProcessor {
                    data: Vec::new(),
                    cache: HashMap::new(),
                }
            }

            fn process(&mut self, input: &[u8]) -> Result<(), io::Error> {
                self.data.extend_from_slice(input);
                Ok(())
            }

            fn clear(&mut self) {
                self.data.clear();
                self.cache.clear();
            }
        }

        enum Status {
            Ready,
            Processing,
            Done,
        }

        trait Processor {
            fn process(&self) -> bool;
            fn reset(&mut self);
        }

        impl Processor for DataProcessor {
            fn process(&self) -> bool {
                !self.data.is_empty()
            }

            fn reset(&mut self) {
                self.clear();
            }
        }
    ''')


# ============================================================================
# RUST HANDLER UNIT TESTS
# ============================================================================

class TestRustHandlerFindDeclaration:
    """Tests for RustHandler.find_declaration()"""

    def test_find_top_level_function(self, rust_handler):
        source = textwrap.dedent('''\
            fn hello() {
                println!("Hello");
            }

            fn goodbye() {
                println!("Goodbye");
            }
        ''')
        loc = rust_handler.find_declaration(source, "hello")
        assert loc is not None
        start, end = loc
        assert "fn hello()" in source[start:end]
        assert "goodbye" not in source[start:end]

    def test_find_struct(self, rust_handler):
        source = textwrap.dedent('''\
            struct Point {
                x: i32,
                y: i32,
            }

            struct Rectangle {
                width: u32,
                height: u32,
            }
        ''')
        loc = rust_handler.find_declaration(source, "Point")
        assert loc is not None
        start, end = loc
        assert "struct Point" in source[start:end]
        assert "x: i32" in source[start:end]
        assert "Rectangle" not in source[start:end]

    def test_find_enum(self, rust_handler):
        source = textwrap.dedent('''\
            enum Color {
                Red,
                Green,
                Blue,
            }
        ''')
        loc = rust_handler.find_declaration(source, "Color")
        assert loc is not None
        start, end = loc
        assert "enum Color" in source[start:end]

    def test_find_impl_block(self, rust_handler):
        source = textwrap.dedent('''\
            struct Foo;

            impl Foo {
                fn bar(&self) {}
            }

            impl Default for Foo {
                fn default() -> Self { Foo }
            }
        ''')
        loc = rust_handler.find_declaration(source, "Foo")
        assert loc is not None
        # Should find the struct, not the impl
        start, end = loc
        assert "struct Foo" in source[start:end]

    def test_find_method_in_impl(self, rust_handler):
        source = textwrap.dedent('''\
            struct Calculator;

            impl Calculator {
                fn add(&self, a: i32, b: i32) -> i32 {
                    a + b
                }

                fn subtract(&self, a: i32, b: i32) -> i32 {
                    a - b
                }
            }
        ''')
        loc = rust_handler.find_declaration(source, "Calculator.add")
        assert loc is not None
        start, end = loc
        assert "fn add" in source[start:end]
        assert "subtract" not in source[start:end]

    def test_find_trait(self, rust_handler):
        source = textwrap.dedent('''\
            trait Drawable {
                fn draw(&self);
                fn bounds(&self) -> (i32, i32, i32, i32);
            }
        ''')
        loc = rust_handler.find_declaration(source, "Drawable")
        assert loc is not None
        start, end = loc
        assert "trait Drawable" in source[start:end]

    def test_find_const(self, rust_handler):
        source = textwrap.dedent('''\
            const MAX_VALUE: u32 = 100;
            const MIN_VALUE: u32 = 0;
        ''')
        loc = rust_handler.find_declaration(source, "MAX_VALUE")
        assert loc is not None
        start, end = loc
        assert "MAX_VALUE" in source[start:end]

    def test_find_static(self, rust_handler):
        source = textwrap.dedent('''\
            static GLOBAL_COUNTER: AtomicUsize = AtomicUsize::new(0);
        ''')
        loc = rust_handler.find_declaration(source, "GLOBAL_COUNTER")
        assert loc is not None

    def test_find_type_alias(self, rust_handler):
        source = textwrap.dedent('''\
            type Result<T> = std::result::Result<T, MyError>;
        ''')
        loc = rust_handler.find_declaration(source, "Result")
        assert loc is not None

    def test_find_mod(self, rust_handler):
        source = textwrap.dedent('''\
            mod tests {
                use super::*;

                fn test_something() {}
            }
        ''')
        loc = rust_handler.find_declaration(source, "tests")
        assert loc is not None
        start, end = loc
        assert "mod tests" in source[start:end]

    def test_find_nonexistent_returns_none(self, rust_handler):
        source = "fn existing() {}"
        loc = rust_handler.find_declaration(source, "nonexistent")
        assert loc is None

    def test_find_nested_nonexistent_returns_none(self, rust_handler):
        source = textwrap.dedent('''\
            impl Foo {
                fn bar() {}
            }
        ''')
        loc = rust_handler.find_declaration(source, "Foo.nonexistent")
        assert loc is None

    def test_find_pub_function(self, rust_handler):
        source = textwrap.dedent('''\
            pub fn public_function() -> i32 {
                42
            }

            fn private_function() -> i32 {
                0
            }
        ''')
        loc = rust_handler.find_declaration(source, "public_function")
        assert loc is not None
        start, end = loc
        assert "pub fn public_function" in source[start:end]

    def test_find_generic_struct(self, rust_handler):
        source = textwrap.dedent('''\
            struct Container<T> {
                item: T,
            }
        ''')
        loc = rust_handler.find_declaration(source, "Container")
        assert loc is not None

    def test_find_async_function(self, rust_handler):
        source = textwrap.dedent('''\
            async fn fetch_data() -> Result<String, Error> {
                Ok(String::new())
            }
        ''')
        loc = rust_handler.find_declaration(source, "fetch_data")
        assert loc is not None


class TestRustHandlerFindHeaderEnd:
    """Tests for RustHandler.find_header_end()"""

    def test_header_ends_at_struct(self, rust_handler):
        source = textwrap.dedent('''\
            use std::io;

            const X: i32 = 5;

            struct Foo {
                x: i32,
            }
        ''')
        header_end = rust_handler.find_header_end(source)
        header = source[:header_end]
        assert "use std::io" in header
        assert "const X" in header
        assert "struct Foo" not in header

    def test_header_ends_at_impl(self, rust_handler):
        source = textwrap.dedent('''\
            use std::fmt;

            impl Display for MyType {
                fn fmt(&self, f: &mut Formatter) -> Result {
                    write!(f, "MyType")
                }
            }
        ''')
        header_end = rust_handler.find_header_end(source)
        header = source[:header_end]
        assert "use std::fmt" in header
        assert "impl Display" not in header

    def test_header_ends_at_enum(self, rust_handler):
        source = textwrap.dedent('''\
            //! Module documentation

            use std::collections::HashMap;

            enum State {
                Active,
                Inactive,
            }
        ''')
        header_end = rust_handler.find_header_end(source)
        header = source[:header_end]
        assert "Module documentation" in header
        assert "use std::collections" in header
        assert "enum State" not in header

    def test_header_includes_functions_and_consts(self, rust_handler):
        """Top-level functions and consts are considered header (utilities)."""
        source = textwrap.dedent('''\
            use std::io;

            fn utility() -> i32 { 42 }

            const MAGIC: i32 = 42;

            struct MainType {
                x: i32,
            }
        ''')
        header_end = rust_handler.find_header_end(source)
        header = source[:header_end]
        # Functions and consts before struct are part of header
        assert "fn utility" in header
        assert "const MAGIC" in header
        assert "struct MainType" not in header

    def test_no_body_types_returns_full_length(self, rust_handler):
        source = textwrap.dedent('''\
            use std::io;

            fn main() {
                println!("Hello");
            }
        ''')
        header_end = rust_handler.find_header_end(source)
        # No struct/enum/impl/trait, so entire file is "header"
        assert header_end == len(source)

    def test_empty_file(self, rust_handler):
        source = ""
        header_end = rust_handler.find_header_end(source)
        assert header_end == 0


class TestRustHandlerGetDeclarationName:
    """Tests for RustHandler.get_declaration_name()"""

    def test_get_function_name(self, rust_handler):
        source = "fn my_function() { }"
        name = rust_handler.get_declaration_name(source, 0, len(source))
        assert name == "my_function"

    def test_get_struct_name(self, rust_handler):
        source = "struct MyStruct { x: i32 }"
        name = rust_handler.get_declaration_name(source, 0, len(source))
        assert name == "MyStruct"

    def test_get_enum_name(self, rust_handler):
        source = "enum MyEnum { A, B, C }"
        name = rust_handler.get_declaration_name(source, 0, len(source))
        assert name == "MyEnum"


# ============================================================================
# EXECUTE CANONICAL TESTS FOR RUST
# ============================================================================

class TestExecuteCanonicalRust:
    """Tests for execute_canonical with Rust files."""

    def test_create_rust_file(self, tmp_path):
        """Test creating a new Rust file."""
        from vibemod.modify_code import execute_canonical

        rust_file = tmp_path / "new_module.rs"
        content = textwrap.dedent('''\
            pub fn hello() {
                println!("Hello from Rust!");
            }
        ''')

        execute_canonical('create_file', [str(rust_file), content, False])

        assert rust_file.exists()
        assert "pub fn hello()" in rust_file.read_text()

    def test_declare_new_function_rust(self, temp_rust_file):
        """Test adding a new function to a Rust file."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            fn existing() -> i32 {
                42
            }
        ''')

        new_function = textwrap.dedent('''\
            fn new_function() -> String {
                String::from("hello")
            }
        ''')

        execute_canonical('declare', [str(rust_file), 'new_function', new_function])

        result = rust_file.read_text()
        assert "fn existing()" in result
        assert "fn new_function()" in result

    def test_declare_replaces_existing_function_rust(self, temp_rust_file):
        """Test replacing an existing Rust function."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            fn target() -> i32 {
                1
            }

            fn other() -> i32 {
                2
            }
        ''')

        replacement = textwrap.dedent('''\
            fn target() -> i32 {
                999
            }
        ''')

        execute_canonical('declare', [str(rust_file), 'target', replacement])

        result = rust_file.read_text()
        assert "999" in result
        assert "fn other()" in result
        # Should not have the old implementation
        assert result.count("fn target()") == 1

    def test_declare_struct_rust(self, temp_rust_file):
        """Test adding a new struct."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            use std::io;
        ''')

        new_struct = textwrap.dedent('''\
            struct Point {
                x: f64,
                y: f64,
            }
        ''')

        execute_canonical('declare', [str(rust_file), 'Point', new_struct])

        result = rust_file.read_text()
        assert "struct Point" in result
        assert "x: f64" in result

    def test_declare_impl_method_rust(self, temp_rust_file):
        """Test adding a method to an impl block."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            struct Counter {
                value: i32,
            }

            impl Counter {
                fn new() -> Self {
                    Counter { value: 0 }
                }
            }
        ''')

        new_method = textwrap.dedent('''\
            fn increment(&mut self) {
                self.value += 1;
            }
        ''')

        execute_canonical('declare', [str(rust_file), 'Counter.increment', new_method])

        result = rust_file.read_text()
        assert "fn increment(&mut self)" in result

    def test_remove_declaration_function_rust(self, temp_rust_file):
        """Test removing a function from a Rust file."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            fn keep_me() -> i32 {
                1
            }

            fn remove_me() -> i32 {
                2
            }

            fn also_keep() -> i32 {
                3
            }
        ''')

        execute_canonical('remove_declaration', [str(rust_file), 'remove_me'])

        result = rust_file.read_text()
        assert "fn keep_me()" in result
        assert "fn also_keep()" in result
        assert "fn remove_me()" not in result

    def test_remove_declaration_struct_rust(self, temp_rust_file):
        """Test removing a struct from a Rust file."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            struct Keep {
                x: i32,
            }

            struct Remove {
                y: i32,
            }
        ''')

        execute_canonical('remove_declaration', [str(rust_file), 'Remove'])

        result = rust_file.read_text()
        assert "struct Keep" in result
        assert "struct Remove" not in result

    def test_remove_declaration_nonexistent_rust(self, temp_rust_file):
        """Test that removing a nonexistent declaration is a no-op."""
        from vibemod.modify_code import execute_canonical

        original_content = '''\
            fn existing() -> i32 {
                42
            }
        '''
        rust_file = temp_rust_file(original_content)

        # Should not raise
        execute_canonical('remove_declaration', [str(rust_file), 'nonexistent'])

        # Content should be unchanged (or minimally changed)
        result = rust_file.read_text()
        assert "fn existing()" in result

    def test_update_header_rust(self, temp_rust_file):
        """Test updating the header of a Rust file."""
        from vibemod.modify_code import execute_canonical

        rust_file = temp_rust_file('''\
            use std::io;

            struct MyStruct {
                x: i32,
            }
        ''')

        new_header = textwrap.dedent('''\
            use std::collections::HashMap;
            use std::sync::Arc;

            const VERSION: &str = "1.0.0";
        ''')

        execute_canonical('update_header', [str(rust_file), new_header])

        result = rust_file.read_text()
        assert "use std::collections::HashMap" in result
        assert "use std::sync::Arc" in result
        assert "const VERSION" in result
        assert "struct MyStruct" in result
        # Old header should be gone
        assert "use std::io" not in result


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestRustIntegration:
    """End-to-end integration tests for Rust support."""

    def test_full_pipeline_create_and_modify_rust(self, tmp_path):
        """Test creating a Rust file and then modifying it."""
        from vibemod.modify_code import extract_command_blocks, canonicalize_command, execute_canonical

        spec = textwrap.dedent('''\
            MMM modification_description MMM
            Create a new Rust module with a struct and impl block.

            MMM create_file MMM
            {path}/lib.rs
            @@@@@@
            use std::fmt;

            struct Greeter {{
                name: String,
            }}

            impl Greeter {{
                fn new(name: &str) -> Self {{
                    Greeter {{ name: name.to_string() }}
                }}
            }}
        '''.format(path=tmp_path))

        blocks = extract_command_blocks(spec)
        for block in blocks:
            for cmd, modargs in canonicalize_command(block):
                execute_canonical(cmd, modargs)

        lib_rs = tmp_path / "lib.rs"
        assert lib_rs.exists()

        content = lib_rs.read_text()
        assert "struct Greeter" in content
        assert "impl Greeter" in content

    def test_full_pipeline_declare_rust_function(self, tmp_path):
        """Test the full pipeline for declaring a Rust function."""
        from vibemod.modify_code import extract_command_blocks, canonicalize_command, execute_canonical

        # First create the file
        rust_file = tmp_path / "module.rs"
        rust_file.write_text(textwrap.dedent('''\
            fn existing() -> i32 {
                0
            }
        '''))

        spec = textwrap.dedent('''\
            MMM declare MMM
            {path}
            @@@@@@
            new_function
            @@@@@@
            fn new_function() -> &'static str {{
                "Hello, Rust!"
            }}
        '''.format(path=rust_file))

        blocks = extract_command_blocks(spec)
        for block in blocks:
            for cmd, modargs in canonicalize_command(block):
                execute_canonical(cmd, modargs)

        content = rust_file.read_text()
        assert "fn existing()" in content
        assert "fn new_function()" in content
        assert '"Hello, Rust!"' in content

    def test_mixed_python_and_rust_modifications(self, tmp_path):
        """Test modifying both Python and Rust files in the same spec."""
        from vibemod.modify_code import extract_command_blocks, canonicalize_command, execute_canonical

        # Create initial files
        py_file = tmp_path / "module.py"
        py_file.write_text("def old_function():\n    pass\n")

        rs_file = tmp_path / "module.rs"
        rs_file.write_text("fn old_function() {}\n")

        spec = textwrap.dedent('''\
            MMM modification_description MMM
            Update both Python and Rust modules with new functions.

            MMM declare MMM
            {py_path}
            @@@@@@
            new_py_function
            @@@@@@
            def new_py_function():
                return "Python"

            MMM declare MMM
            {rs_path}
            @@@@@@
            new_rs_function
            @@@@@@
            fn new_rs_function() -> &'static str {{
                "Rust"
            }}
        '''.format(py_path=py_file, rs_path=rs_file))

        blocks = extract_command_blocks(spec)
        for block in blocks:
            for cmd, modargs in canonicalize_command(block):
                execute_canonical(cmd, modargs)

        py_content = py_file.read_text()
        assert "def new_py_function" in py_content

        rs_content = rs_file.read_text()
        assert "fn new_rs_function" in rs_content


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestRustErrorHandling:
    """Tests for error handling in Rust operations."""

    def test_unsupported_extension_raises(self, tmp_path):
        """Test that unsupported file extensions raise ValueError."""
        from vibemod.handlers import get_handler

        with pytest.raises(ValueError, match="Unsupported file extension"):
            get_handler(str(tmp_path / "file.go"))

    def test_declare_on_nonexistent_file_creates_it(self, tmp_path):
        """Test that declaring on a nonexistent Rust file creates it."""
        from vibemod.modify_code import execute_canonical

        rust_file = tmp_path / "new_file.rs"
        assert not rust_file.exists()

        content = "fn hello() {}"
        execute_canonical('declare', [str(rust_file), 'hello', content])

        assert rust_file.exists()
        assert "fn hello()" in rust_file.read_text()

    def test_remove_declaration_on_nonexistent_file_noop(self, tmp_path):
        """Test that removing from nonexistent file doesn't raise."""
        from vibemod.modify_code import execute_canonical

        rust_file = tmp_path / "nonexistent.rs"

        # Should not raise
        execute_canonical('remove_declaration', [str(rust_file), 'anything'])

        # File should still not exist
        assert not rust_file.exists()


# ============================================================================
# HANDLER REGISTRY TESTS
# ============================================================================

class TestHandlerRegistry:
    """Tests for the handler registry system."""

    def test_python_handler_registered(self):
        """Test that Python handler is registered for .py files."""
        from vibemod.handlers import get_handler
        from vibemod.handlers.python_handler import PythonHandler

        handler = get_handler("test.py")
        assert isinstance(handler, PythonHandler)

    def test_rust_handler_registered(self):
        """Test that Rust handler is registered for .rs files."""
        from vibemod.handlers import get_handler
        from vibemod.handlers.rust_handler import RustHandler

        handler = get_handler("test.rs")
        assert isinstance(handler, RustHandler)

    def test_get_handler_with_path(self):
        """Test that get_handler works with full paths."""
        from vibemod.handlers import get_handler

        handler_py = get_handler("/some/path/to/file.py")
        handler_rs = get_handler("/another/path/module.rs")

        assert handler_py is not None
        assert handler_rs is not None
