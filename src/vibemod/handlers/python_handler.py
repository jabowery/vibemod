# src/vibemod/handlers/python_handler.py
"""Python language handler using tree-sitter."""

from typing import Optional, Tuple, List

from .base import LanguageHandler, register_handler

PYTHON_DECL_TYPES = frozenset({
    'function_definition',
    'class_definition',
    'decorated_definition',
    'assignment',
    'expression_statement',
})

PYTHON_BODY_TYPES = frozenset({
    'class_definition',
    'function_definition',
})

class PythonHandler(LanguageHandler):
    """Handler for Python source files using Python's built-in ast module."""

    def content_starts_with_attr_or_doc(self, code: str) -> bool:
        """Check if code starts with decorators or docstrings."""
        stripped = code.lstrip()
        return stripped.startswith('@') or stripped.startswith('"""') or stripped.startswith("'''")

    def get_decl_types(self) -> frozenset:
        """Return Python declaration node types."""
        return PYTHON_DECL_TYPES

    def validate_syntax(self, content: str, original_content: str=None) -> Optional[str]:
        """Validate Python syntax using tree-sitter.

    Returns None if valid, or an error message if invalid.
    """
        root = self._parse(content)
        errors = []
        self._collect_errors(root, content, errors)
        if not errors:
            return None
        if original_content is not None:
            original_root = self._parse(original_content)
            original_errors = []
            self._collect_errors(original_root, original_content, original_errors)
            if len(errors) <= len(original_errors):
                return None
        error_lines = ['Syntax errors detected in resulting code:']
        for error_text, line_num, col, context in errors[:5]:
            error_lines.append(f'  Line {line_num}, column {col}: {error_text}')
            error_lines.append(f'    Context: {context}')
        if len(errors) > 5:
            error_lines.append(f'  ... and {len(errors) - 5} more errors')
        error_lines.append('')
        error_lines.append('The modification has been rejected to prevent invalid code.')
        return '\n'.join(error_lines)

    def _collect_errors(self, node: 'Node', content: str, errors: list) -> None:
        """Recursively collect ERROR nodes from parse tree."""
        if node.type == 'ERROR' or node.is_missing:
            line_num = node.start_point[0] + 1
            col = node.start_point[1] + 1
            start_char = self._byte_to_char(content, node.start_byte)
            end_char = self._byte_to_char(content, node.end_byte)
            ctx_start = max(0, start_char - 20)
            ctx_end = min(len(content), end_char + 20)
            context = content[ctx_start:ctx_end].replace('\n', '\\n')
            if ctx_start > 0:
                context = '...' + context
            if ctx_end < len(content):
                context = context + '...'
            error_text = 'Unexpected syntax' if node.type == 'ERROR' else f'Missing {node.type}'
            errors.append((error_text, line_num, col, context))
        for child in self._get_children(node):
            self._collect_errors(child, content, errors)

    def find_all_declarations(self, content: str, target_path: str) -> List[Tuple[int, int]]:
        """Find all declarations matching the target path."""
        parts = target_path.split('.')
        root = self._parse(content)
        results = []
        self._find_all_in_scope(root, parts, content, results)
        return results

    def _find_all_in_scope(self, scope_node: 'Node', parts: List[str], content: str, results: List[Tuple[int, int]]) -> None:
        """Find all matching declarations in a scope."""
        if not parts:
            return
        target_name = parts[0]
        remaining = parts[1:]
        for child in self._get_children(scope_node):
            if child.type not in PYTHON_DECL_TYPES:
                continue
            name = self._get_declaration_name(child, content)
            if name == target_name:
                if remaining:
                    body = child.child_by_field_name('body')
                    if body:
                        self._find_all_in_scope(body, remaining, content, results)
                else:
                    start = self._byte_to_char(content, child.start_byte)
                    end = self._byte_to_char(content, child.end_byte)
                    results.append((start, end))

    def _find_in_scope(self, scope_node: 'Node', parts: List[str], content: str) -> Optional[Tuple[int, int]]:
        """Find declaration in a scope, handling nested paths."""
        if not parts:
            return None
        target_name = parts[0]
        remaining = parts[1:]
        for child in self._get_children(scope_node):
            if child.type not in PYTHON_DECL_TYPES:
                continue
            name = self._get_declaration_name(child, content)
            if name == target_name:
                if remaining:
                    body = child.child_by_field_name('body')
                    if body:
                        return self._find_in_scope(body, remaining, content)
                    return None
                else:
                    start = self._byte_to_char(content, child.start_byte)
                    end = self._byte_to_char(content, child.end_byte)
                    return (start, end)
        return None

    def _get_declaration_name(self, node: 'Node', content: str) -> Optional[str]:
        """Extract the name from a declaration node."""
        if node.type == 'decorated_definition':
            for child in self._get_children(node):
                if child.type in ('function_definition', 'class_definition'):
                    return self._get_declaration_name(child, content)
            return None
        if node.type in ('function_definition', 'class_definition'):
            name_node = node.child_by_field_name('name')
            if name_node:
                return name_node.text.decode('utf-8')
            return None
        if node.type == 'assignment':
            left = node.child_by_field_name('left')
            if left and left.type == 'identifier':
                return left.text.decode('utf-8')
            for child in self._get_children(node):
                if child.type == 'identifier':
                    return child.text.decode('utf-8')
            return None
        if node.type == 'expression_statement':
            for child in self._get_children(node):
                if child.type == 'assignment':
                    return self._get_declaration_name(child, content)
            return None
        return None

    def _get_children(self, node: 'Node') -> list:
        """Get children of a node as a list."""
        children = node.children
        if hasattr(children, '__iter__') and (not isinstance(children, list)):
            return list(children)
        return children

    def _byte_to_char(self, content: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to Python character offset."""
        if byte_offset <= 0:
            return 0
        encoded = content.encode('utf-8')
        if byte_offset >= len(encoded):
            return len(content)
        return len(encoded[:byte_offset].decode('utf-8'))

    def _parse(self, content: str) -> 'Node':
        """Parse Python source and return the root node."""
        tree = self._parser.parse(content.encode('utf-8'))
        return tree.root_node

    def __init__(self):
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser
        self._language = Language(tspython.language())
        self._parser = Parser(self._language)

    def validate_single_declaration(self, content: str) -> Optional[str]:
        """Validate that content contains exactly one top-level declaration.

    Returns None if valid, or an error message if invalid.
    """
        content = content.strip()
        if not content:
            return 'Declare content is empty. Each declare directive must contain exactly one declaration.'
        root = self._parse(content)
        declarations = []
        for child in self._get_children(root):
            if child.type == 'ERROR':
                return 'Declare content has syntax errors and cannot be parsed.'
            if child.type in PYTHON_DECL_TYPES:
                name = self._get_declaration_name(child, content)
                if name:
                    declarations.append((child.type, name))
            elif child.type == 'comment':
                continue
        if len(declarations) == 0:
            return 'Declare content contains no valid Python declaration. Expected: def, class, or assignment.'
        if len(declarations) > 1:
            decl_names = [f"{t.replace('_definition', '').replace('_', ' ')} '{n}'" for t, n in declarations]
            return f"Declare content contains {len(declarations)} declarations, but only one is allowed per directive.\nFound: {', '.join(decl_names)}\nSplit these into separate MMM declare MMM blocks."
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
        """Find the span of a declaration by name.

    Returns (start_char, end_char) or None if not found.
    """
        parts = target_path.split('.')
        root = self._parse(content)
        return self._find_in_scope(root, parts, content)

    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first class or function definition).

    Returns character offset.
    """
        root = self._parse(content)
        for child in self._get_children(root):
            if child.type in PYTHON_BODY_TYPES or child.type == 'decorated_definition':
                return self._byte_to_char(content, child.start_byte)
        return len(content)

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

    def update_header(self, source: str, new_header: str) -> str:
        """
        Replace the header section of a Python source file.

        The header is everything before the first class or function definition.
        """
        header_end = self.find_header_end(source)
        new_header_clean = new_header.strip() + '\n\n'
        return new_header_clean + source[header_end:]

register_handler('.py', PythonHandler())

def insert_new_declaration(self, source: str, content: str) -> str:
    """Insert a new declaration at the header_end position.

    Places new declarations after imports but before existing
    class/function definitions.
    """
    header_end = self.find_header_end(source)
    before = source[:header_end].rstrip()
    after = source[header_end:].lstrip()
    if after:
        new_source = before + '\n\n' + content + '\n\n' + after
    else:
        new_source = before + '\n\n' + content + '\n' if before else content + '\n'
    # Collapse multiple blank lines
    import re
    new_source = re.sub('\n\n\n+', '\n\n', new_source)
    return new_source

def modify_declaration(
    self,
    file_path: str,
    source: str,
    target_path: str,
    content: Optional[str],
    remove: bool,
    debug_dump_func=None
) -> str:
    """Python-specific declaration modification."""
    source_before = source

    def validate_and_return(new_source: str) -> str:
        syntax_error = self.validate_syntax(new_source, original_content=source_before)
        if syntax_error:
            if debug_dump_func:
                debug_dir = debug_dump_func(
                    file_path=file_path,
                    target_path=target_path,
                    content=content,
                    source_before=source_before,
                    source_after=new_source,
                    error_message=syntax_error,
                    remove=remove
                )
                raise ValueError(f'Modification would create syntactically invalid Python code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
            raise ValueError(f'Modification would create syntactically invalid Python code:\n{syntax_error}')
        return new_source

    if remove:
        spans = self.find_all_declarations(source, target_path)
        if not spans:
            return source
        spans.sort(key=lambda s: s[0], reverse=True)
        new_source = source
        for start, end in spans:
            before = new_source[:start].rstrip()
            after = new_source[end:].lstrip()
            new_source = before + '\n\n' + after
        import re
        new_source = re.sub('\n\n\n+', '\n\n', new_source)
        return validate_and_return(new_source)

    if content is None:
        raise ValueError('Content required for declare operation')

    content = textwrap.dedent(content).strip()

    single_decl_error = self.validate_single_declaration(content)
    if single_decl_error:
        if debug_dump_func:
            debug_dir = debug_dump_func(
                file_path=file_path,
                target_path=target_path,
                content=content,
                source_before=source_before,
                source_after=content,
                error_message=single_decl_error,
                remove=remove
            )
            raise ValueError(f'Invalid declare content:\n{single_decl_error}\n\nDebug files written to: {debug_dir}')
        raise ValueError(f'Invalid declare content:\n{single_decl_error}')

    spans = self.find_all_declarations(source, target_path)
    if spans:
        adjusted_spans = [
            self.adjust_span_for_attributes(source, s, e, content)
            for s, e in spans
        ]
        adjusted_spans.sort(key=lambda s: s[0], reverse=True)
        new_source = source
        for start, end in adjusted_spans:
            new_source = new_source[:start] + content + new_source[end:]
        return validate_and_return(new_source)

    new_source = self.insert_new_declaration(source, content)
    return validate_and_return(new_source)
