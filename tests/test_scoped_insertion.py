"""Tests for generalized scoped symbol declaration with @after/@before anchors."""

import pytest
import tempfile
import textwrap
import os

class TestPythonScopedInsertion:
    """Tests for Python scoped symbol insertion."""

    def test_python_get_symbol_type_assignment(self):
        """Test identifying assignment symbol type."""
        from vibemod.handlers.python_handler import PythonHandler

        handler = PythonHandler()
        sym_type = handler.get_symbol_type("x = 42")

        assert sym_type.name == "Assignment"

    def test_python_get_symbol_type_function(self):
        """Test identifying function symbol type."""
        from vibemod.handlers.python_handler import PythonHandler

        handler = PythonHandler()
        sym_type = handler.get_symbol_type("def foo(): pass")

        assert sym_type.name == "Function"

    def test_python_symbols_conflict_assignment_assignment(self):
        """Test that assignments conflict with assignments in Python."""
        from vibemod.handlers.python_handler import PythonHandler, SymbolType

        handler = PythonHandler()

        assert handler.symbols_conflict(SymbolType.Assignment, SymbolType.Assignment) == True

    def test_python_symbols_conflict_assignment_function(self):
        """Test that assignments conflict with functions in Python (same namespace)."""
        from vibemod.handlers.python_handler import PythonHandler, SymbolType

        handler = PythonHandler()

        # In Python, all names share the same namespace
        assert handler.symbols_conflict(SymbolType.Assignment, SymbolType.Function) == True

    def test_python_insert_after_in_function(self):
        """Test inserting a statement after anchor in Python function."""
        from vibemod.handlers.python_handler import PythonHandler

        handler = PythonHandler()
        content = '''def my_fn():
    x = 1
    y = 2
'''
        new_content = handler.modify_declaration(
            file_path="test.py",
            source=content,
            target_path="my_fn.@after:x = 1",
            content="inserted = 42",
            remove=False
        )

        lines = new_content.split('\n')
        x_line = next(i for i, l in enumerate(lines) if 'x = 1' in l)
        inserted_line = next(i for i, l in enumerate(lines) if 'inserted = 42' in l)
        y_line = next(i for i, l in enumerate(lines) if 'y = 2' in l)

        assert x_line < inserted_line < y_line

class TestScopedInsertion:
    """Tests for generalized scoped symbol insertion with @after/@before anchors."""

    def test_parse_scoped_target_after(self):
        """Test parsing of scope.@after:expr target syntax."""
        from vibemod.handlers.rust_handler import parse_target_path

        target = parse_target_path("my_function.@after:let x = 1;")

        assert target.scope_path == "my_function"
        assert target.anchor_type == "after"
        assert target.anchor_expr == "let x = 1;"
        assert target.is_scoped_insertion == True

    def test_parse_scoped_target_before(self):
        """Test parsing of scope.@before:expr target syntax."""
        from vibemod.handlers.rust_handler import parse_target_path

        target = parse_target_path("my_function.@before:let x = 1;")

        assert target.scope_path == "my_function"
        assert target.anchor_type == "before"
        assert target.anchor_expr == "let x = 1;"
        assert target.is_scoped_insertion == True

    def test_parse_nested_scope_target(self):
        """Test parsing of nested scope like impl_block.method.@after:expr."""
        from vibemod.handlers.rust_handler import parse_target_path

        target = parse_target_path("impl:MyStruct.my_method.@after:let y = 2;")

        assert target.scope_path == "impl:MyStruct.my_method"
        assert target.anchor_type == "after"
        assert target.anchor_expr == "let y = 2;"

    def test_rust_get_symbol_type_let(self):
        """Test identifying let binding symbol type."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        sym_type = handler.get_symbol_type("let x = 42;")

        assert sym_type.name == "LetBinding"

    def test_rust_get_symbol_type_const(self):
        """Test identifying const item symbol type."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        sym_type = handler.get_symbol_type("const X: i32 = 42;")

        assert sym_type.name == "ConstItem"

    def test_rust_get_symbol_type_function(self):
        """Test identifying function symbol type."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        sym_type = handler.get_symbol_type("fn foo() {}")

        assert sym_type.name == "Function"

    def test_rust_symbols_conflict_let_let(self):
        """Test that let bindings conflict with let bindings."""
        from vibemod.handlers.rust_handler import RustHandler, SymbolType

        handler = RustHandler()

        assert handler.symbols_conflict(SymbolType.LetBinding, SymbolType.LetBinding) == True

    def test_rust_symbols_no_conflict_let_fn(self):
        """Test that let bindings don't conflict with functions (different namespaces)."""
        from vibemod.handlers.rust_handler import RustHandler, SymbolType

        handler = RustHandler()

        assert handler.symbols_conflict(SymbolType.LetBinding, SymbolType.Function) == False

    def test_rust_find_scope_function(self):
        """Test finding function scope boundaries."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''
fn other() {}

fn target_fn() {
    let x = 1;
    let y = 2;
}

fn another() {}
'''
        span = handler.find_scope(content, "target_fn")

        assert span is not None
        scope_text = content[span[0]:span[1]]
        assert "let x = 1;" in scope_text
        assert "let y = 2;" in scope_text
        assert "fn other()" not in scope_text

    def test_rust_find_statement_in_scope(self):
        """Test finding a statement within a scope."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''
fn my_fn() {
    let x = 1;
    let y = compute();
    let z = 3;
}
'''
        scope_span = handler.find_scope(content, "my_fn")
        stmt_span = handler.find_statement_in_scope(
            content, 
            scope_span[0], 
            scope_span[1], 
            "let y = compute();"
        )

        assert stmt_span is not None
        assert content[stmt_span[0]:stmt_span[1]].strip() == "let y = compute();"

    def test_rust_find_symbol_in_scope(self):
        """Test finding an existing symbol in a scope."""
        from vibemod.handlers.rust_handler import RustHandler, SymbolType

        handler = RustHandler()
        content = '''
fn my_fn() {
    let x = 1;
    let target = "old value";
    let z = 3;
}
'''
        scope_span = handler.find_scope(content, "my_fn")
        sym_span = handler.find_symbol_in_scope(
            content,
            scope_span[0],
            scope_span[1],
            "target",
            SymbolType.LetBinding
        )

        assert sym_span is not None
        assert 'let target = "old value"' in content[sym_span[0]:sym_span[1]]

    def test_rust_insert_after_anchor(self):
        """Test inserting a statement after an anchor statement."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''fn my_fn() {
    let x = 1;
    let y = 2;
}
'''
        new_content = handler.modify_declaration(
            file_path="test.rs",
            source=content,
            target_path="my_fn.@after:let x = 1;",
            content="let inserted = 42;",
            remove=False
        )

        lines = new_content.split('\n')
        x_line = next(i for i, l in enumerate(lines) if 'let x = 1' in l)
        inserted_line = next(i for i, l in enumerate(lines) if 'let inserted = 42' in l)
        y_line = next(i for i, l in enumerate(lines) if 'let y = 2' in l)

        assert x_line < inserted_line < y_line

    def test_rust_insert_before_anchor(self):
        """Test inserting a statement before an anchor statement."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''fn my_fn() {
    let x = 1;
    let y = 2;
}
'''
        new_content = handler.modify_declaration(
            file_path="test.rs",
            source=content,
            target_path="my_fn.@before:let y = 2;",
            content="let inserted = 42;",
            remove=False
        )

        lines = new_content.split('\n')
        x_line = next(i for i, l in enumerate(lines) if 'let x = 1' in l)
        inserted_line = next(i for i, l in enumerate(lines) if 'let inserted = 42' in l)
        y_line = next(i for i, l in enumerate(lines) if 'let y = 2' in l)

        assert x_line < inserted_line < y_line

    def test_rust_insert_replaces_conflicting_symbol(self):
        """Test that inserting a symbol removes existing conflicting declaration."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''fn my_fn() {
    let x = 1;
    let target = "old";
    let y = 2;
}
'''
        new_content = handler.modify_declaration(
            file_path="test.rs",
            source=content,
            target_path="my_fn.@after:let x = 1;",
            content='let target = "new";',
            remove=False
        )

        # Old declaration should be gone
        assert 'let target = "old"' not in new_content
        # New declaration should be present
        assert 'let target = "new"' in new_content
        # Should be after x
        lines = new_content.split('\n')
        x_line = next(i for i, l in enumerate(lines) if 'let x = 1' in l)
        target_line = next(i for i, l in enumerate(lines) if 'let target = "new"' in l)
        assert x_line < target_line

    def test_rust_insert_preserves_indentation(self):
        """Test that inserted content has correct indentation."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''fn my_fn() {
    let x = 1;
}
'''
        new_content = handler.modify_declaration(
            file_path="test.rs",
            source=content,
            target_path="my_fn.@after:let x = 1;",
            content="let y = 2;",
            remove=False
        )

        for line in new_content.split('\n'):
            if 'let y = 2' in line:
                indent = len(line) - len(line.lstrip())
                assert indent == 4, f"Expected 4 spaces indent, got {indent}"
                break

    def test_rust_insert_in_nested_scope(self):
        """Test insertion in a method inside an impl block."""
        from vibemod.handlers.rust_handler import RustHandler

        handler = RustHandler()
        content = '''impl MyStruct {
    fn my_method(&self) {
        let x = 1;
        let y = 2;
    }
}
'''
        new_content = handler.modify_declaration(
            file_path="test.rs",
            source=content,
            target_path="impl:MyStruct.my_method.@after:let x = 1;",
            content="let inserted = 42;",
            remove=False
        )

        assert "let inserted = 42;" in new_content
        lines = new_content.split('\n')
        x_line = next(i for i, l in enumerate(lines) if 'let x = 1' in l)
        inserted_line = next(i for i, l in enumerate(lines) if 'let inserted = 42' in l)
        assert x_line < inserted_line

    def test_rust_anchor_not_found_raises(self):
        """Test that missing anchor raises appropriate error."""
        from vibemod.handlers.rust_handler import RustHandler
        import pytest

        handler = RustHandler()
        content = '''fn my_fn() {
    let x = 1;
}
'''
        with pytest.raises(ValueError, match="anchor.*not found"):
            handler.modify_declaration(
                file_path="test.rs",
                source=content,
                target_path="my_fn.@after:let nonexistent = 0;",
                content="let y = 2;",
                remove=False
            )

    def test_rust_scope_not_found_raises(self):
        """Test that missing scope raises appropriate error."""
        from vibemod.handlers.rust_handler import RustHandler
        import pytest

        handler = RustHandler()
        content = '''fn other_fn() {
    let x = 1;
}
'''
        with pytest.raises(ValueError, match="scope.*not found"):
            handler.modify_declaration(
                file_path="test.rs",
                source=content,
                target_path="nonexistent_fn.@after:let x = 1;",
                content="let y = 2;",
                remove=False
            )
