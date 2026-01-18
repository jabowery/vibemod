# src/vibemod/handlers/python_handler.py
"""Python language handler using the ast module."""

import ast
from typing import Optional, Tuple, List

from .base import LanguageHandler, register_handler


class PythonHandler(LanguageHandler):
    """Handler for Python source files using Python's built-in ast module."""

    def _is_decl(self, node: ast.AST) -> bool:
        """Check if a node is a declaration (function or class)."""
        return isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))

    def _get_scope(self, tree: ast.AST, parts: List[str]) -> Optional[ast.AST]:
        """Traverse the AST to find the scope for a nested dotted target."""
        scopes = [tree]
        for part in parts:
            found = None
            current = scopes[-1]
            children = current.body if hasattr(current, 'body') else []
            for node in children:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == part:
                        found = node
                        break
            if found is None:
                return None
            scopes.append(found)
        return scopes[-1]

    def find_declaration(
        self, content: str, target_path: str
    ) -> Optional[Tuple[int, int]]:
        """Find a declaration using Python's ast module."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return None

        parts = target_path.split('.')
        if not parts:
            return None

        # Navigate to the parent scope
        if len(parts) > 1:
            scope = self._get_scope(tree, parts[:-1])
            if scope is None:
                return None
        else:
            scope = tree

        name = parts[-1]
        body = scope.body if hasattr(scope, 'body') else []

        # Find the declaration by name
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    # Calculate byte offsets from line numbers
                    lines = content.splitlines(keepends=True)

                    # Account for decorators
                    if hasattr(node, 'decorator_list') and node.decorator_list:
                        start_line = node.decorator_list[0].lineno - 1
                    else:
                        start_line = node.lineno - 1

                    end_line = node.end_lineno

                    start_byte = sum(len(lines[i]) for i in range(start_line))
                    end_byte = sum(len(lines[i]) for i in range(end_line))

                    return (start_byte, end_byte)

        return None

    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first class/function definition)."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return len(content)

        decl_nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        if not decl_nodes:
            return len(content)

        # Find the earliest declaration, accounting for decorators
        start_lines = []
        for node in decl_nodes:
            if hasattr(node, 'decorator_list') and node.decorator_list:
                start_lines.append(node.decorator_list[0].lineno)
            else:
                start_lines.append(node.lineno)

        first_decl_lineno = min(start_lines)
        lines = content.splitlines(keepends=True)
        header_end = sum(len(lines[i]) for i in range(first_decl_lineno - 1))

        return header_end

    def get_declaration_name(
        self, content: str, start: int, end: int
    ) -> Optional[str]:
        """Extract the declaration name from a code region."""
        snippet = content[start:end]
        try:
            tree = ast.parse(snippet)
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
        except SyntaxError:
            pass
        return None


# Register the handler for .py files
register_handler('.py', PythonHandler())

