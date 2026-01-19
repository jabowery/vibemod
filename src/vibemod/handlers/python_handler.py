# src/vibemod/handlers/python_handler.py
"""Python language handler using the ast module."""

import ast
from typing import Optional, Tuple, List

from .base import LanguageHandler, register_handler


class PythonHandler(LanguageHandler):
    """Handler for Python source files using Python's built-in ast module."""

    def validate_single_declaration(self, content: str) -> Optional[str]:
        """
    Validate that content contains exactly one top-level declaration.

    Returns None if valid, or an error message if invalid.
    """
        content = content.strip()
        if not content:
            return 'Declare content is empty. Each declare directive must contain exactly one declaration.'
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return f'Invalid content syntax: {e}'
        declarations = []
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                declarations.append(f"{node.__class__.__name__.replace('Def', '').lower()} '{node.name}'")
            elif isinstance(node, ast.Assign):
                names = self._get_assignment_names(node)
                for name in names:
                    declarations.append(f"assignment '{name}'")
            elif isinstance(node, ast.AnnAssign):
                names = self._get_assignment_names(node)
                for name in names:
                    declarations.append(f"annotated assignment '{name}'")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
        if len(declarations) == 0:
            return 'Declare content contains no valid Python declaration. Expected: def, class, or assignment statement.'
        if len(declarations) > 1:
            return f"Declare content contains {len(declarations)} declarations, but only one is allowed per directive.\nFound: {', '.join(declarations)}\nSplit these into separate MMM declare MMM blocks."
        return None

    def _get_assignment_names(self, node: ast.AST) -> List[str]:
        """Extract variable names from an assignment node."""
        names = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.append(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.append(node.target.id)
        return names

    def _is_decl(self, node: ast.AST) -> bool:
        """Check if a node is a declaration (function, class, or assignment)."""
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return True
        return False

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

    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """Find a declaration using Python's ast module."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return None
        parts = target_path.split('.')
        if not parts:
            return None
        if len(parts) > 1:
            scope = self._get_scope(tree, parts[:-1])
            if scope is None:
                return None
        else:
            scope = tree
        name = parts[-1]
        body = scope.body if hasattr(scope, 'body') else []
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    lines = content.splitlines(keepends=True)
                    if hasattr(node, 'decorator_list') and node.decorator_list:
                        start_line = node.decorator_list[0].lineno - 1
                    else:
                        start_line = node.lineno - 1
                    end_line = node.end_lineno
                    start_byte = sum((len(lines[i]) for i in range(start_line)))
                    end_byte = sum((len(lines[i]) for i in range(end_line)))
                    return (start_byte, end_byte)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                assign_names = self._get_assignment_names(node)
                if name in assign_names:
                    lines = content.splitlines(keepends=True)
                    start_line = node.lineno - 1
                    end_line = node.end_lineno
                    start_byte = sum((len(lines[i]) for i in range(start_line)))
                    end_byte = sum((len(lines[i]) for i in range(end_line)))
                    return (start_byte, end_byte)
        return None

    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first class/function definition)."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return len(content)
        decl_nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        if not decl_nodes:
            return len(content)
        start_lines = []
        for node in decl_nodes:
            if hasattr(node, 'decorator_list') and node.decorator_list:
                start_lines.append(node.decorator_list[0].lineno)
            else:
                start_lines.append(node.lineno)
        first_decl_lineno = min(start_lines)
        lines = content.splitlines(keepends=True)
        header_end = sum((len(lines[i]) for i in range(first_decl_lineno - 1)))
        return header_end

    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
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
register_handler('.py', PythonHandler())