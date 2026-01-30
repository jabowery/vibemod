# src/vibemod/handlers/rust_handler.py
"""
Rust language handler using tree-sitter-rust.

Implements the vibemod Rust specification:
- Extended target path grammar
- Multi-match replace/remove semantics  
- Insertion anchors
- Signature-based disambiguation
- Rich error diagnostics
- Scoped insertion with @after/@before anchors
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Set
import textwrap
from .base import LanguageHandler, register_handler

try:
    import tree_sitter_rust as tsrust
    from tree_sitter import Language, Parser, Node
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# =============================================================================
# RUST DECLARATION TYPES (exported for use by modify_code.py)
# =============================================================================

RUST_DECL_TYPES = frozenset({
    'function_item',
    'struct_item',
    'enum_item',
    'impl_item',
    'trait_item',
    'mod_item',
    'type_item',
    'const_item',
    'static_item',
    'union_item',
    'macro_definition',
    'use_declaration',
    'inner_attribute_item',
})

# Item types that must be unique by name within a module scope
# (excludes impl_item which can have multiple blocks for same type)
RUST_UNIQUE_ITEM_TYPES = frozenset({
    'mod_item',
    'struct_item',
    'enum_item',
    'trait_item',
    'type_item',
    'function_item',  # Free functions (not methods in impl blocks)
    'const_item',     # Module-level consts (not associated consts)
    'static_item',
    'union_item',
    'macro_definition',
})

RUST_BODY_TYPES = frozenset({
    'struct_item',
    'enum_item',
    'impl_item',
    'trait_item',
    'mod_item',
    'union_item',
})

RUST_ASSOCIATED_ITEM_TYPES = frozenset({
    'function_item',
    'const_item', 
    'type_item',
})


# =============================================================================
# SYMBOL TYPE (for scoped insertion conflict detection)
# =============================================================================

class SymbolType:
    """Represents the type of a symbol in Rust."""
    LetBinding = "LetBinding"
    ConstItem = "ConstItem"
    StaticItem = "StaticItem"
    Function = "Function"
    Struct = "Struct"
    Enum = "Enum"
    Impl = "Impl"
    Trait = "Trait"
    Mod = "Mod"
    Type = "Type"
    Use = "Use"
    Macro = "Macro"
    
    def __init__(self, name: str):
        self.name = name
    
    def __eq__(self, other):
        if isinstance(other, SymbolType):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return False
    
    def __hash__(self):
        return hash(self.name)
    
    def __repr__(self):
        return f"SymbolType({self.name})"


# =============================================================================
# TARGET PATH PARSING
# =============================================================================

# Import the grammar-driven parser
from vibemod.target_parser import parse_target_path, TargetPath


# =============================================================================
# INDEXED ITEM (for declaration lookup)
# =============================================================================

@dataclass
class IndexedItem:
    """An indexed Rust item with full metadata for disambiguation."""
    kind: str
    name: Optional[str]
    module_path: List[str]
    start_byte: int
    end_byte: int
    impl_type: Optional[str] = None
    impl_trait: Optional[str] = None
    attrs: List[str] = field(default_factory=list)
    attrs_fingerprint: str = ''
    parent_impl: Optional['IndexedItem'] = None
    associated_items: List['IndexedItem'] = field(default_factory=list)
    node: Optional['Node'] = None

    @property
    def canonical_path(self) -> str:
        """Build canonical path string for diagnostics."""
        parts = []
        if self.module_path:
            parts.append('::'.join(self.module_path) + '::')
        if self.kind == 'impl_item':
            if self.impl_trait:
                parts.append(f'impl({self.impl_trait} for {self.impl_type})')
            else:
                parts.append(f'impl({self.impl_type})')
        elif self.name:
            parts.append(self.name)
        if self.attrs_fingerprint:
            parts.append(self.attrs_fingerprint)
        return ''.join(parts)

    def matches_attr_filter(self, attr_filter: Optional[str]) -> bool:
        """Check if this item matches the attribute filter."""
        if attr_filter is None:
            return True
        return attr_filter in self.attrs


# =============================================================================
# RUST HANDLER
# =============================================================================

class RustHandler(LanguageHandler):
    """
    Handler for Rust source files using tree-sitter-rust.
    
    Implements the full vibemod Rust specification with:
    - Extended target path grammar
    - Multi-match semantics
    - Insertion anchors
    - Signature-based disambiguation
    - Rich error diagnostics
    - Scoped insertion with @after/@before anchors
    """

    def __init__(self):
        import tree_sitter_rust_orchard as tsrust
        from tree_sitter import Language, Parser
        self._language = Language(tsrust.language())
        self._parser = Parser(self._language)

    def _parse(self, content: str) -> 'Node':
        """Parse Rust source and return the root node."""
        tree = self._parser.parse(content.encode('utf-8'))
        return tree.root_node

    def _byte_to_char(self, content: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to Python character offset."""
        if byte_offset <= 0:
            return 0
        encoded = content.encode('utf-8')
        if byte_offset >= len(encoded):
            return len(content)
        return len(encoded[:byte_offset].decode('utf-8'))

    def _get_children(self, node: 'Node') -> List['Node']:
        """Get children of a node as a list (handles tree-sitter API differences)."""
        children = node.children
        if hasattr(children, '__iter__') and (not isinstance(children, list)):
            return list(children)
        return children

    def _get_declaration_name(self, node: 'Node', content: str) -> Optional[str]:
        """Extract the name from a declaration node."""
        if node.type == 'impl_item':
            return self._get_impl_type_name(node)
        if node.type == 'use_declaration':
            return self._get_use_declaration_name(node)
        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')
        return None

    def _get_impl_type_name(self, impl_node: 'Node') -> Optional[str]:
        """Extract the type name from an impl block."""
        type_node = impl_node.child_by_field_name('type')
        if type_node:
            if type_node.type == 'type_identifier':
                return type_node.text.decode('utf-8')
            elif type_node.type == 'generic_type':
                ident = type_node.child_by_field_name('type')
                if ident:
                    return ident.text.decode('utf-8')
        for child in impl_node.children:
            if child.type == 'type_identifier':
                return child.text.decode('utf-8')
        return None

    def _get_impl_trait_name(self, impl_node: 'Node') -> Optional[str]:
        """Extract the trait name from a trait impl block."""
        trait_node = impl_node.child_by_field_name('trait')
        if trait_node:
            if trait_node.type == 'type_identifier':
                return trait_node.text.decode('utf-8')
            elif trait_node.type == 'scoped_type_identifier':
                return trait_node.text.decode('utf-8')
            elif trait_node.type == 'generic_type':
                ident = trait_node.child_by_field_name('type')
                if ident:
                    return ident.text.decode('utf-8')
        return None

    def _get_use_declaration_name(self, node: 'Node') -> Optional[str]:
        """Extract the imported name from a use_declaration node."""
        for child in self._get_children(node):
            if child.type == 'use_as_clause':
                alias = child.child_by_field_name('alias')
                if alias:
                    return alias.text.decode('utf-8')
        arg = node.child_by_field_name('argument')
        if arg is None:
            for child in self._get_children(node):
                if child.type in ('scoped_identifier', 'identifier', 'scoped_use_list', 'use_wildcard'):
                    arg = child
                    break
        if arg is None:
            return None
        if arg.type == 'identifier':
            return arg.text.decode('utf-8')
        elif arg.type == 'scoped_identifier':
            name = arg.child_by_field_name('name')
            if name:
                return name.text.decode('utf-8')
            for child in reversed(self._get_children(arg)):
                if child.type == 'identifier':
                    return child.text.decode('utf-8')
        return None

    # =========================================================================
    # SYMBOL TYPE DETECTION (for scoped insertion)
    # =========================================================================

    def get_symbol_type(self, content: str) -> Optional[SymbolType]:
        """Determine the symbol type of a declaration/statement."""
        content = content.strip()
        root = self._parse(content)
        
        for child in self._get_children(root):
            node_type = child.type
            
            if node_type == 'let_declaration':
                return SymbolType(SymbolType.LetBinding)
            elif node_type == 'const_item':
                return SymbolType(SymbolType.ConstItem)
            elif node_type == 'static_item':
                return SymbolType(SymbolType.StaticItem)
            elif node_type == 'function_item':
                return SymbolType(SymbolType.Function)
            elif node_type == 'struct_item':
                return SymbolType(SymbolType.Struct)
            elif node_type == 'enum_item':
                return SymbolType(SymbolType.Enum)
            elif node_type == 'impl_item':
                return SymbolType(SymbolType.Impl)
            elif node_type == 'trait_item':
                return SymbolType(SymbolType.Trait)
            elif node_type == 'mod_item':
                return SymbolType(SymbolType.Mod)
            elif node_type == 'type_item':
                return SymbolType(SymbolType.Type)
            elif node_type == 'use_declaration':
                return SymbolType(SymbolType.Use)
            elif node_type == 'macro_definition':
                return SymbolType(SymbolType.Macro)
            elif node_type == 'expression_statement':
                for subchild in self._get_children(child):
                    if subchild.type == 'let_declaration':
                        return SymbolType(SymbolType.LetBinding)
        
        return None

    def get_symbol_name(self, content: str) -> Optional[str]:
        """Extract the symbol name from a declaration/statement."""
        content = content.strip()
        root = self._parse(content)
        
        for child in self._get_children(root):
            node_type = child.type
            
            if node_type == 'let_declaration':
                pattern = child.child_by_field_name('pattern')
                if pattern:
                    if pattern.type == 'identifier':
                        return pattern.text.decode('utf-8')
                    elif pattern.type == 'tuple_pattern':
                        return pattern.text.decode('utf-8')
                return None
            elif node_type in ('function_item', 'struct_item', 'enum_item', 
                              'trait_item', 'mod_item', 'type_item',
                              'const_item', 'static_item'):
                name_node = child.child_by_field_name('name')
                if name_node:
                    return name_node.text.decode('utf-8')
            elif node_type == 'use_declaration':
                return self._get_use_declaration_name(child)
            elif node_type == 'impl_item':
                return self._get_impl_type_name(child)
        
        return None

    def symbols_conflict(self, type1: SymbolType, type2: SymbolType) -> bool:
        """Return True if two symbol types conflict in the same Rust scope."""
        if type1 is None or type2 is None:
            return False
        
        # Let bindings conflict with let bindings
        if type1.name == SymbolType.LetBinding and type2.name == SymbolType.LetBinding:
            return True
        
        # Value namespace conflicts
        value_types = {SymbolType.LetBinding, SymbolType.ConstItem, SymbolType.StaticItem}
        if type1.name in value_types and type2.name in value_types:
            return True
        
        # Type namespace conflicts
        type_types = {SymbolType.Struct, SymbolType.Enum, SymbolType.Trait, SymbolType.Type}
        if type1.name in type_types and type2.name in type_types:
            return True
        
        # Functions conflict with functions
        if type1.name == SymbolType.Function and type2.name == SymbolType.Function:
            return True
        
        return False

    # =========================================================================
    # SCOPED INSERTION SUPPORT
    # =========================================================================

    def find_scope(self, content: str, scope_path: str) -> Optional[Tuple[int, int]]:
        """Find the character span of a named scope (function, impl method, etc.)."""
        span = self.find_declaration(content, scope_path)
        if span is None:
            return None
        
        decl_text = content[span[0]:span[1]]
        root = self._parse(decl_text)
        
        for child in self._get_children(root):
            if child.type in ('function_item', 'impl_item', 'mod_item', 'trait_item'):
                body = child.child_by_field_name('body')
                if body:
                    body_start = span[0] + self._byte_to_char(decl_text, body.start_byte)
                    body_end = span[0] + self._byte_to_char(decl_text, body.end_byte)
                    return (body_start, body_end)
        
        return span

    def find_statement_in_scope(self, content: str, scope_start: int, scope_end: int, statement: str) -> Optional[Tuple[int, int]]:
        """Find a statement within a scope by matching its text."""
        scope_content = content[scope_start:scope_end]
        statement = statement.strip()
        
        # Try exact match
        idx = scope_content.find(statement)
        if idx != -1:
            return (scope_start + idx, scope_start + idx + len(statement))
        
        # Try without trailing semicolon
        if statement.endswith(';'):
            statement_no_semi = statement[:-1].strip()
            idx = scope_content.find(statement_no_semi)
            if idx != -1:
                end_idx = idx + len(statement_no_semi)
                while end_idx < len(scope_content) and scope_content[end_idx] in ' \t':
                    end_idx += 1
                if end_idx < len(scope_content) and scope_content[end_idx] == ';':
                    end_idx += 1
                return (scope_start + idx, scope_start + end_idx)
        
        # Try normalized whitespace match
        normalized_stmt = ' '.join(statement.split())
        root = self._parse(scope_content)
        
        for child in self._get_children(root):
            if child.type == 'block':
                for stmt in self._get_children(child):
                    stmt_start = self._byte_to_char(scope_content, stmt.start_byte)
                    stmt_end = self._byte_to_char(scope_content, stmt.end_byte)
                    stmt_text = scope_content[stmt_start:stmt_end]
                    normalized_found = ' '.join(stmt_text.split())
                    if normalized_found == normalized_stmt or normalized_found == normalized_stmt.rstrip(';'):
                        return (scope_start + stmt_start, scope_start + stmt_end)
        
        return None

    def find_statement_by_regex(
        self, 
        content: str, 
        scope_start: int, 
        scope_end: int, 
        pattern: str, 
        occurrence: int = 1
    ) -> Optional[Tuple[int, int]]:
        """
        Find a statement within a scope by regex pattern matching.
        
        The regex is matched against each statement in the scope. When a statement
        contains a match, the entire statement's span is returned (not just the matched part).
        
        Args:
            content: Full source content
            scope_start: Start offset of scope in content
            scope_end: End offset of scope in content  
            pattern: Regex pattern to search for (\\s matches any whitespace including newlines)
            occurrence: Which match to return (1-based, default 1)
            
        Returns:
            Tuple of (start, end) offsets for the Nth matching statement, or None if not found.
        """
        scope_content = content[scope_start:scope_end]
        
        # Compile the regex with DOTALL so \s matches newlines
        try:
            regex = re.compile(pattern, re.DOTALL)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern}': {e}")
        
        # Parse the scope to get individual statements
        root = self._parse(scope_content)
        
        matching_statements: List[Tuple[int, int]] = []
        
        # Statement types that are actual statements (not containers)
        STATEMENT_TYPES = frozenset({
            'let_declaration', 'expression_statement', 'return_expression',
            'if_expression', 'while_expression', 'for_expression', 
            'loop_expression', 'match_expression', 'macro_invocation'
        })
        
        # Container types we should always recurse into
        CONTAINER_TYPES = frozenset({
            'source_file', 'block', 'declaration_list'
        })
        
        def find_statements_in_node(node):
            """Recursively find statements that match the regex."""
            # Always recurse into container types
            if node.type in CONTAINER_TYPES:
                for child in node.children:
                    find_statements_in_node(child)
                return
            
            # For expression_statement, check if it contains a block (in which case recurse)
            # Otherwise treat it as a statement to match
            if node.type == 'expression_statement':
                # Check if this expression_statement contains a block as its main content
                for child in node.children:
                    if child.type == 'block':
                        # This is a bare block `{ ... }` wrapped in expression_statement
                        # Recurse into the block
                        find_statements_in_node(child)
                        return
                # Not a block container - treat as regular statement
                stmt_start = self._byte_to_char(scope_content, node.start_byte)
                stmt_end = self._byte_to_char(scope_content, node.end_byte)
                stmt_text = scope_content[stmt_start:stmt_end]
                
                if regex.search(stmt_text):
                    matching_statements.append((scope_start + stmt_start, scope_start + stmt_end))
                return
            
            # Check if this is a statement-like node
            if node.type in STATEMENT_TYPES:
                stmt_start = self._byte_to_char(scope_content, node.start_byte)
                stmt_end = self._byte_to_char(scope_content, node.end_byte)
                stmt_text = scope_content[stmt_start:stmt_end]
                
                if regex.search(stmt_text):
                    matching_statements.append((scope_start + stmt_start, scope_start + stmt_end))
                return  # Don't recurse into matched statements
            
            # For other node types, recurse into children
            for child in node.children:
                find_statements_in_node(child)
        
        # Start the search
        for child in self._get_children(root):
            find_statements_in_node(child)
        
        # Return the Nth match (1-based)
        if 1 <= occurrence <= len(matching_statements):
            return matching_statements[occurrence - 1]
        
        return None



    def find_symbol_in_scope(self, content: str, scope_start: int, scope_end: int, symbol_name: str, symbol_type: SymbolType) -> Optional[Tuple[int, int]]:
        """Find an existing symbol declaration within a scope."""
        scope_content = content[scope_start:scope_end]
        root = self._parse(scope_content)

        def check_node(node) -> Optional[Tuple[int, int]]:
            # Check if this node is a matching declaration
            if node.type == 'let_declaration':
                stmt_start = self._byte_to_char(scope_content, node.start_byte)
                stmt_end = self._byte_to_char(scope_content, node.end_byte)
                stmt_text = scope_content[stmt_start:stmt_end]

                stmt_type = self.get_symbol_type(stmt_text)
                stmt_name = self.get_symbol_name(stmt_text)

                if stmt_name == symbol_name and self.symbols_conflict(stmt_type, symbol_type):
                    return (scope_start + stmt_start, scope_start + stmt_end)

            # Recurse into children
            for child in self._get_children(node):
                result = check_node(child)
                if result:
                    return result

            return None

        return check_node(root)


    def get_scope_indent(self, content: str, scope_start: int, scope_end: int) -> int:
        """Get the indentation level for statements in a scope."""
        scope_content = content[scope_start:scope_end]
        
        lines = scope_content.split('\n')
        for line in lines[1:]:  # Skip first line (opening brace)
            stripped = line.lstrip()
            if stripped and not stripped.startswith('//') and not stripped.startswith('}'):
                return len(line) - len(stripped)
        
        # Default: find indent of scope start and add 4
        line_start = content.rfind('\n', 0, scope_start) + 1
        scope_line = content[line_start:scope_start]
        base_indent = len(scope_line) - len(scope_line.lstrip())
        return base_indent + 4

    # =========================================================================
    # TARGET PATH NORMALIZATION
    # =========================================================================

    def normalize_target_path(self, target_path: str, content: str = None) -> str:
        """Normalize a target path by stripping redundant type prefixes."""
        target = target_path.strip()
        simple_prefixes = [
            'fn ', 'struct ', 'enum ', 'const ', 'static ',
            'type ', 'trait ', 'mod ', 'use ', 'pub fn ',
            'pub struct ', 'pub enum ', 'pub const ', 'pub static ',
            'pub type ', 'pub trait ', 'pub mod ', 'pub use ',
            'async fn ', 'pub async fn '
        ]
        for prefix in simple_prefixes:
            if target.lower().startswith(prefix.lower()):
                name = target[len(prefix):].strip()
                if '(' in name:
                    name = name[:name.index('(')].strip()
                if '<' in name:
                    name = name[:name.index('<')].strip()
                return name
        if target.lower().startswith('impl '):
            rest = target[5:].strip()
            return 'impl:' + rest
        return target

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate_single_declaration(self, content: str, allow_statements: bool = False) -> Optional[str]:
        """
        Validate that content contains exactly one top-level declaration.
        
        Args:
            content: The code to validate
            allow_statements: If True, also allow statements (for scoped insertion),
                             including let declarations, if/while/for, function calls, etc.
        
        Returns None if valid, or an error message if invalid.
        """
        content = content.strip()
        if not content:
            return 'Declare content is empty. Each declare directive must contain exactly one declaration.'
        root = self._parse(content)
        declarations = []
        for child in self._get_children(root):
            if child.type in RUST_DECL_TYPES:
                declarations.append(child)
            elif allow_statements and child.type in ('let_declaration', 'expression_statement'):
                declarations.append(child)
            elif child.type == 'attribute_item':
                continue
            elif child.type in ('line_comment', 'block_comment'):
                continue
            elif child.type == 'ERROR':
                return 'Declare content has syntax errors and cannot be parsed.'
        if len(declarations) == 0:
            if allow_statements:
                return 'Declare content contains no valid Rust declaration or statement.'
            return 'Declare content contains no valid Rust declaration. Expected: fn, struct, enum, impl, trait, mod, type, const, static, macro, or use.'
        if len(declarations) > 1:
            decl_names = []
            for d in declarations:
                name_node = d.child_by_field_name('name')
                if name_node:
                    decl_names.append(f"{d.type.replace('_item', '').replace('_declaration', '')} '{name_node.text.decode('utf-8')}'")
                elif d.type == 'impl_item':
                    impl_type = self._get_impl_type_name(d)
                    decl_names.append(f"impl {impl_type or '?'}")
                elif d.type == 'use_declaration':
                    use_name = self._get_use_declaration_name(d)
                    decl_names.append(f"use '{use_name or '?'}'")
                elif d.type == 'let_declaration':
                    pattern = d.child_by_field_name('pattern')
                    if pattern:
                        decl_names.append(f"let '{pattern.text.decode('utf-8')}'")
                    else:
                        decl_names.append('let')
                elif d.type == 'expression_statement':
                    # Try to identify the kind of expression
                    for subchild in d.children:
                        if subchild.type == 'if_expression':
                            decl_names.append('if statement')
                            break
                        elif subchild.type == 'while_expression':
                            decl_names.append('while statement')
                            break
                        elif subchild.type == 'for_expression':
                            decl_names.append('for statement')
                            break
                        elif subchild.type == 'match_expression':
                            decl_names.append('match statement')
                            break
                        elif subchild.type == 'call_expression':
                            decl_names.append('function call')
                            break
                    else:
                        decl_names.append('expression')
                else:
                    decl_names.append(d.type.replace('_item', '').replace('_declaration', ''))
            return f"Declare content contains {len(declarations)} declarations, but only one is allowed per directive.\nFound: {', '.join(decl_names)}\nSplit these into separate MMM declare MMM blocks."
        return None

    def _collect_errors(self, node: 'Node', content: str, errors: List = None) -> List[tuple]:
        """Recursively collect ERROR and MISSING nodes from parse tree."""
        if errors is None:
            errors = []
        if node.type == 'ERROR' or node.is_missing:
            start_point = node.start_point
            line_num = start_point[0] + 1
            col = start_point[1] + 1
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
        return errors

    def validate_syntax(self, content: str, original_content: str = None) -> Optional[str]:
        """Validate Rust syntax using tree-sitter."""
        root = self._parse(content)
        errors = self._collect_errors(root, content)
        if not errors:
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

    def validate_no_illegal_duplicates(self, content: str) -> Optional[str]:
        """Validate that the content has no illegal duplicate declarations."""
        items = self._build_item_index(content)
        seen: dict[tuple, IndexedItem] = {}
        duplicates: List[tuple[str, str, IndexedItem, IndexedItem]] = []
        for item in items:
            if item.parent_impl is not None:
                continue
            if item.kind == 'impl_item':
                continue
            if item.kind not in RUST_UNIQUE_ITEM_TYPES:
                continue
            if item.name is None:
                continue
            key = (tuple(item.module_path), item.kind, item.name)
            if key in seen:
                existing = seen[key]
                duplicates.append((item.kind, item.name, existing, item))
            else:
                seen[key] = item
        if not duplicates:
            return None
        lines = ['Illegal duplicate declarations detected:']
        for kind, name, first, second in duplicates:
            kind_name = kind.replace('_item', '').replace('_', ' ')
            lines.append(f"  - {kind_name} '{name}' declared at bytes {first.start_byte} and {second.start_byte}")
        lines.append('')
        lines.append('Rust requires these items to be unique within a module scope.')
        lines.append('The modification has been rejected to prevent invalid code.')
        return '\n'.join(lines)

    # =========================================================================
    # DECLARATION MODIFICATION
    # =========================================================================

    def modify_declaration(
        self,
        file_path: str,
        source: str,
        target_path: str,
        content: Optional[str],
        remove: bool,
        debug_dump_func=None
    ) -> str:
        """
        Rust-specific declaration modification.
        
        Handles:
        - impl block method insertion
        - Rust-specific target path parsing (impl:Type.method)
        - Tolerant target syntax (fn foo(), struct Bar, etc.)
        - Scoped insertion with @after/@before anchors
        """
        source_before = source
        
        # Normalize target path to handle "fn foo()" -> "foo" etc.
        target_path = self.normalize_target_path(target_path, content)
        target = parse_target_path(target_path)
        
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
                    raise ValueError(f'Modification would create syntactically invalid Rust code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
                raise ValueError(f'Modification would create syntactically invalid Rust code:\n{syntax_error}')
            
            dup_error = self.validate_no_illegal_duplicates(new_source)
            if dup_error:
                raise ValueError(f'Modification would create invalid Rust code:\n{dup_error}')
            
            return new_source
        
        # Handle scoped insertion (@after/@before within a scope)
        if target.is_scoped_insertion:
            if content is None:
                raise ValueError('Content required for scoped insertion')
            
            content = textwrap.dedent(content).strip()
            
            # For scoped insertion, allow let statements
            single_decl_error = self.validate_single_declaration(content, allow_statements=True)
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
            
            # Find the scope
            scope_span = self.find_scope(source, target.scope_path)
            if scope_span is None:
                raise ValueError(f"Scope '{target.scope_path}' not found in {file_path}")
            
            # Find the anchor statement (regex or literal)
            if target.anchor_is_regex:
                anchor_span = self.find_statement_by_regex(
                    source, scope_span[0], scope_span[1], 
                    target.anchor_expr, 
                    target.anchor_occurrence or 1
                )
                if anchor_span is None:
                    raise ValueError(
                        f"Anchor regex /{target.anchor_expr}/ (occurrence @{target.anchor_occurrence or 1}) "
                        f"not found in scope '{target.scope_path}' in {file_path}"
                    )
            else:
                anchor_span = self.find_statement_in_scope(
                    source, scope_span[0], scope_span[1], target.anchor_expr
                )
                if anchor_span is None:
                    raise ValueError(f"Anchor '{target.anchor_expr}' not found in scope '{target.scope_path}' in {file_path}")
            
            # Check for conflicting existing symbol
            new_symbol_type = self.get_symbol_type(content)
            new_symbol_name = self.get_symbol_name(content)
            
            new_source = source
            if new_symbol_name and new_symbol_type:
                existing_span = self.find_symbol_in_scope(
                    new_source, scope_span[0], scope_span[1],
                    new_symbol_name, new_symbol_type
                )
                if existing_span:
                    # Remove existing declaration
                    before = new_source[:existing_span[0]].rstrip()
                    after = new_source[existing_span[1]:].lstrip()
                    if not before.endswith('\n'):
                        before += '\n'
                    new_source = before + after
                    
                    # Recalculate spans
                    offset = len(source) - len(new_source)
                    if anchor_span[0] > existing_span[0]:
                        anchor_span = (anchor_span[0] - offset, anchor_span[1] - offset)
                    scope_span = self.find_scope(new_source, target.scope_path)
            
            # Get indentation
            indent = self.get_scope_indent(new_source, scope_span[0], scope_span[1])
            indent_str = ' ' * indent
            
            # Indent the content
            indented_lines = []
            for line in content.split('\n'):
                if line.strip():
                    indented_lines.append(indent_str + line)
                else:
                    indented_lines.append(line)
            indented_content = '\n'.join(indented_lines)
            
            # Insert before or after anchor, or replace
            if target.anchor_type == 'after':
                insert_pos = anchor_span[1]
                while insert_pos < len(new_source) and new_source[insert_pos] not in '\n':
                    insert_pos += 1
                if insert_pos < len(new_source) and new_source[insert_pos] == '\n':
                    insert_pos += 1
                new_source = new_source[:insert_pos] + indented_content + '\n' + new_source[insert_pos:]
            elif target.anchor_type == 'replace':
                # Replace the matched statement with the new content
                # Find the line start for proper replacement
                replace_start = anchor_span[0]
                while replace_start > 0 and new_source[replace_start - 1] != '\n':
                    replace_start -= 1
                replace_end = anchor_span[1]
                while replace_end < len(new_source) and new_source[replace_end] not in '\n':
                    replace_end += 1
                if replace_end < len(new_source) and new_source[replace_end] == '\n':
                    replace_end += 1
                new_source = new_source[:replace_start] + indented_content + '\n' + new_source[replace_end:]
            else:  # before
                insert_pos = anchor_span[0]
                while insert_pos > 0 and new_source[insert_pos - 1] != '\n':
                    insert_pos -= 1
                new_source = new_source[:insert_pos] + indented_content + '\n' + new_source[insert_pos:]
            
            return validate_and_return(new_source)
        
        # Handle removal
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
            new_source = re.sub(r'\n\n\n+', '\n\n', new_source)
            return validate_and_return(new_source)
        
        if content is None:
            raise ValueError('Content required for declare operation')
        
        content = textwrap.dedent(content).strip()
        
        # Unwrap method from impl block if user wrapped it
        if target.is_impl_target and target.associated_name:
            unwrapped = self.unwrap_method_from_impl(content, target.associated_name)
            if unwrapped is not None:
                content = unwrapped
        
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
        
        # Handle insertion anchors
        if target.is_insertion:
            insertion_point = self.get_insertion_point(source, target_path)
            if insertion_point is None:
                diagnostic = self.format_candidates_diagnostic(source, target_path)
                raise ValueError(f'Cannot determine insertion point in {file_path}.\n{diagnostic}')
            before = source[:insertion_point].rstrip()
            after = source[insertion_point:].lstrip()
            new_source = before + '\n\n' + content + '\n\n' + after
            new_source = re.sub(r'\n\n\n+', '\n\n', new_source)
            return validate_and_return(new_source)
        
        # Handle impl method targets
        if target.is_impl_target and target.associated_name:
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
            
            # No existing method - insert into impl block
            # First, check if content is a full impl block (common mistake)
            content_symbol_type = self.get_symbol_type(content)
            if content_symbol_type and content_symbol_type.name == SymbolType.Impl:
                raise ValueError(
                    f"Content appears to be a full impl block, but target path "
                    f"'impl:{target.impl_type}.{target.associated_name}' expects a method body.\n"
                    f"Either:\n"
                    f"  1. Remove the 'impl {target.impl_type} {{ ... }}' wrapper and provide just the method, or\n"
                    f"  2. Use an insertion anchor like '@after:impl:{target.impl_type}' to insert a new impl block.\n"
                    f"File: {file_path}"
                )
            
            insertion_point = self.get_impl_block_insertion_point(source, target_path)
            if insertion_point is None:
                diagnostic = self.format_candidates_diagnostic(source, target_path)
                # Provide more specific error based on what we found
                items = self._build_item_index(source)
                
                # First check: did user use @0 (common mistake - selectors are 1-based)
                if target.occurrence == 0:
                    # Check how many impl blocks actually exist
                    impl_target_no_occurrence = TargetPath(
                        module_path=target.module_path,
                        impl_type=target.impl_type,
                        impl_trait=target.impl_trait,
                        attr_filter=target.attr_filter,
                        occurrence=None  # No occurrence filter
                    )
                    actual_matches = self._find_matches(items, impl_target_no_occurrence)
                    if len(actual_matches) > 0:
                        raise ValueError(
                            f"Invalid occurrence selector @0 in {file_path}: selectors are 1-based. "
                            f"Use @1 for the first impl block, @2 for the second, etc. "
                            f"Found {len(actual_matches)} impl block(s) for '{target.impl_type}'.\n{diagnostic}"
                        )
                
                # Check if occurrence is out of range
                if target.occurrence is not None:
                    impl_target_no_occurrence = TargetPath(
                        module_path=target.module_path,
                        impl_type=target.impl_type,
                        impl_trait=target.impl_trait,
                        attr_filter=target.attr_filter,
                        occurrence=None
                    )
                    actual_matches = self._find_matches(items, impl_target_no_occurrence)
                    if len(actual_matches) > 0:
                        raise ValueError(
                            f"Cannot insert method '{target.associated_name}' in {file_path}: "
                            f"@{target.occurrence} is out of range. Found {len(actual_matches)} impl block(s) for '{target.impl_type}' "
                            f"(valid selectors: @1 to @{len(actual_matches)}).\n{diagnostic}"
                        )
                
                # No impl block found at all
                raise ValueError(f"Cannot insert method '{target.associated_name}' in {file_path}: no impl block found for '{target.impl_type}'.\n{diagnostic}")
            
            line_start = source.rfind('\n', 0, insertion_point) + 1
            line_content = source[line_start:insertion_point]
            base_indent = len(line_content) - len(line_content.lstrip())
            indent = ' ' * (base_indent + 4)
            indented_content = '\n'.join(
                indent + line if line.strip() else line
                for line in content.split('\n')
            )
            new_source = source[:insertion_point] + '\n' + indented_content + '\n' + source[insertion_point:]
            return validate_and_return(new_source)
        
        # Standard declaration replacement
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
        
        # Declaration not found - append to end of file
        # This applies to both regular items (struct, fn, etc.) and impl blocks
        new_source = source.rstrip() + '\n\n' + content + '\n' if source.strip() else content + '\n'
        return validate_and_return(new_source)

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def unwrap_method_from_impl(self, content: str, expected_method: str) -> Optional[str]:
        """
        If content is an impl block containing a single method matching expected_method,
        extract and return just the method. Otherwise return None.
        """
        content = content.strip()
        root = self._parse(content)
        impl_node = None
        for child in self._get_children(root):
            if child.type == 'impl_item':
                impl_node = child
                break
        if impl_node is None:
            return None
        body = impl_node.child_by_field_name('body')
        if body is None:
            for child in self._get_children(impl_node):
                if child.type == 'declaration_list':
                    body = child
                    break
        if body is None:
            return None
        methods = []
        for child in self._get_children(body):
            if child.type == 'function_item':
                name_node = child.child_by_field_name('name')
                if name_node:
                    method_name = name_node.text.decode('utf-8')
                    methods.append((method_name, child))
        if len(methods) == 1 and methods[0][0] == expected_method:
            node = methods[0][1]
            start_byte = node.start_byte
            for child in self._get_children(body):
                if child.end_byte <= node.start_byte:
                    if child.type in ('attribute_item', 'line_comment', 'block_comment'):
                        between_start = child.end_byte
                        between_end = node.start_byte
                        between_text = content[self._byte_to_char(content, between_start):self._byte_to_char(content, between_end)]
                        if between_text.strip() == '' or all((c.type in ('attribute_item', 'line_comment', 'block_comment') for c in self._get_children(body) if child.end_byte <= c.start_byte < node.start_byte)):
                            start_byte = min(start_byte, child.start_byte)
            start_char = self._byte_to_char(content, start_byte)
            end_char = self._byte_to_char(content, node.end_byte)
            return content[start_char:end_char].strip()
        return None

    def unwrap_content_if_needed(self, content: str, target_path: str) -> str:
        """Unwrap method from impl block if user wrapped it unnecessarily."""
        target = parse_target_path(target_path)
        if target.is_impl_target and target.associated_name:
            unwrapped = self.unwrap_method_from_impl(content, target.associated_name)
            if unwrapped is not None:
                return unwrapped
        return content

    def content_starts_with_attr_or_doc(self, code: str) -> bool:
        """Check if code starts with attributes (#[...]) or doc comments (///)."""
        stripped = code.lstrip()
        return (stripped.startswith('#[') or 
                stripped.startswith('///') or 
                stripped.startswith('//!') or 
                stripped.startswith('/**') or 
                stripped.startswith('/*!'))

    def adjust_span_for_attributes(self, source: str, span_start: int, span_end: int, new_content: str) -> Tuple[int, int]:
        """Adjust span if original has attrs/docs but replacement doesn't."""
        if self.content_starts_with_attr_or_doc(new_content):
            return (span_start, span_end)
        original_span_text = source[span_start:span_end]
        if not self.content_starts_with_attr_or_doc(original_span_text):
            return (span_start, span_end)
        # Find where actual declaration starts in span
        root = self._parse(original_span_text)
        for child in self._get_children(root):
            if child.type in RUST_DECL_TYPES:
                decl_offset = self._byte_to_char(original_span_text, child.start_byte)
                if decl_offset > 0:
                    return (span_start + decl_offset, span_end)
        return (span_start, span_end)

    def is_insertion_target(self, target_path: str) -> bool:
        """Check if target path is an insertion anchor."""
        target = parse_target_path(target_path)
        return target.is_insertion

    def get_decl_types(self) -> frozenset:
        """Return Rust declaration node types."""
        return RUST_DECL_TYPES

    # =========================================================================
    # INDEXING
    # =========================================================================

    def _build_item_index(self, content: str) -> List[IndexedItem]:
        """Build a complete index of all items in the file, including associated items."""
        root = self._parse(content)
        items: List[IndexedItem] = []
        self._index_scope(root, [], items, content)
        all_items: List[IndexedItem] = []
        for item in items:
            all_items.append(item)
            all_items.extend(item.associated_items)
        return all_items

    def _index_scope(self, node: 'Node', module_path: List[str], items: List[IndexedItem], content: str) -> None:
        """Recursively index items in a scope."""
        pending_attrs: List[str] = []
        attr_start: Optional[int] = None
        doc_comment_start: Optional[int] = None
        children = self._get_children(node)
        for child in children:
            if child.type == 'attribute_item':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                attr_text = content[start_char:end_char].strip()
                pending_attrs.append(attr_text)
                if attr_start is None:
                    attr_start = start_char
                doc_comment_start = None
                continue
            if child.type == 'line_comment':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                comment_text = content[start_char:end_char]
                if comment_text.startswith('///') or comment_text.startswith('//!'):
                    if doc_comment_start is None:
                        doc_comment_start = start_char
                continue
            if child.type == 'block_comment':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                comment_text = content[start_char:end_char]
                if comment_text.startswith('/**') or comment_text.startswith('/*!'):
                    if doc_comment_start is None:
                        doc_comment_start = start_char
                continue
            if child.type in RUST_DECL_TYPES:
                if doc_comment_start is not None:
                    start_char = doc_comment_start
                elif pending_attrs:
                    start_char = attr_start
                else:
                    start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                item = self._index_item(child, module_path, pending_attrs, start_char, end_char, content)
                if item:
                    items.append(item)
                    if child.type in ('impl_item', 'trait_item'):
                        self._index_associated_items(child, item, content)
                pending_attrs = []
                attr_start = None
                doc_comment_start = None
            elif child.type == 'mod_item':
                name_node = child.child_by_field_name('name')
                if name_node:
                    mod_name = name_node.text.decode('utf-8')
                    body = child.child_by_field_name('body')
                    if body:
                        self._index_scope(body, module_path + [mod_name], items, content)
                pending_attrs = []
                attr_start = None
                doc_comment_start = None
            elif child.type not in ('line_comment', 'block_comment'):
                pending_attrs = []
                attr_start = None
                doc_comment_start = None

    def _index_item(self, node: 'Node', module_path: List[str], attrs: List[str], start_char: int, end_char: int, content: str) -> Optional[IndexedItem]:
        """Create an IndexedItem from a tree-sitter node."""
        item = IndexedItem(
            kind=node.type,
            name=None,
            module_path=list(module_path),
            start_byte=start_char,
            end_byte=end_char,
            attrs=list(attrs),
            attrs_fingerprint=' '.join(sorted(attrs)),
            node=node
        )
        if node.type == 'impl_item':
            item.impl_type = self._get_impl_type_name(node)
            item.impl_trait = self._get_impl_trait_name(node)
        elif node.type == 'use_declaration':
            item.name = self._get_use_declaration_name(node)
        elif node.type in ('const_item', 'static_item'):
            name_node = node.child_by_field_name('name')
            if name_node:
                item.name = name_node.text.decode('utf-8')
        elif node.type == 'type_item':
            name_node = node.child_by_field_name('name')
            if name_node:
                item.name = name_node.text.decode('utf-8')
        else:
            name_node = node.child_by_field_name('name')
            if name_node:
                item.name = name_node.text.decode('utf-8')
        return item

    def _index_associated_items(self, impl_node: 'Node', parent_item: IndexedItem, content: str) -> None:
        """Index associated items within an impl or trait block."""
        body = impl_node.child_by_field_name('body')
        if body is None:
            for child in self._get_children(impl_node):
                if child.type == 'declaration_list':
                    body = child
                    break
        if body is None:
            return
        pending_attrs: List[str] = []
        attr_start: Optional[int] = None
        doc_comment_start: Optional[int] = None
        children = self._get_children(body)
        for child in children:
            if child.type == 'attribute_item':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                attr_text = content[start_char:end_char].strip()
                pending_attrs.append(attr_text)
                if attr_start is None:
                    attr_start = start_char
                continue
            if child.type == 'line_comment':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                comment_text = content[start_char:end_char]
                if comment_text.startswith('///') or comment_text.startswith('//!'):
                    if doc_comment_start is None:
                        doc_comment_start = start_char
                continue
            if child.type == 'block_comment':
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                comment_text = content[start_char:end_char]
                if comment_text.startswith('/**') or comment_text.startswith('/*!'):
                    if doc_comment_start is None:
                        doc_comment_start = start_char
                continue
            if child.type in RUST_ASSOCIATED_ITEM_TYPES:
                name_node = child.child_by_field_name('name')
                if name_node:
                    if doc_comment_start is not None:
                        start_char = doc_comment_start
                    elif attr_start is not None:
                        start_char = attr_start
                    else:
                        start_char = self._byte_to_char(content, child.start_byte)
                    end_char = self._byte_to_char(content, child.end_byte)
                    assoc_item = IndexedItem(
                        kind=child.type,
                        name=name_node.text.decode('utf-8'),
                        module_path=parent_item.module_path,
                        start_byte=start_char,
                        end_byte=end_char,
                        attrs=list(pending_attrs),
                        attrs_fingerprint=' '.join(sorted(pending_attrs)),
                        parent_impl=parent_item,
                        node=child
                    )
                    parent_item.associated_items.append(assoc_item)
                pending_attrs = []
                attr_start = None
                doc_comment_start = None
            elif child.type not in ('line_comment', 'block_comment'):
                pending_attrs = []
                attr_start = None
                doc_comment_start = None

    def _find_matches(self, items: List[IndexedItem], target: TargetPath) -> List[IndexedItem]:
        """Find all items matching the target path."""
        candidates: List[IndexedItem] = []
        for item in items:
            if self._item_matches(item, target):
                candidates.append(item)
        if target.occurrence is not None:
            if 1 <= target.occurrence <= len(candidates):
                return [candidates[target.occurrence - 1]]
            return []
        return candidates

    def _item_matches(self, item: IndexedItem, target: TargetPath) -> bool:
        """Check if an item matches the target path."""
        if target.module_path:
            if item.module_path[:len(target.module_path)] != target.module_path:
                return False
        if not item.matches_attr_filter(target.attr_filter):
            return False
        if target.is_impl_target:
            if target.associated_name:
                if item.parent_impl is None:
                    return False
                if item.name != target.associated_name:
                    return False
                parent = item.parent_impl
                if parent.impl_type != target.impl_type:
                    return False
                if target.is_trait_impl:
                    if parent.impl_trait != target.impl_trait:
                        return False
                if target.attr_filter and (not parent.matches_attr_filter(target.attr_filter)):
                    return False
                return True
            else:
                if item.kind != 'impl_item':
                    return False
                if item.impl_type != target.impl_type:
                    return False
                if target.is_trait_impl and item.impl_trait != target.impl_trait:
                    return False
                return True
        if target.item_name:
            return item.name == target.item_name
        return False

    # =========================================================================
    # DECLARATION FINDING
    # =========================================================================

    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """Find declaration(s) matching the target path."""
        target = parse_target_path(target_path)
        if target.is_insertion:
            return None
        items = self._build_item_index(content)
        matches = self._find_matches(items, target)
        if not matches:
            return None
        return (matches[0].start_byte, matches[0].end_byte)

    def find_all_declarations(self, content: str, target_path: str) -> List[Tuple[int, int]]:
        """Find ALL declarations matching the target path."""
        target = parse_target_path(target_path)
        if target.is_insertion:
            return []
        items = self._build_item_index(content)
        matches = self._find_matches(items, target)
        return [(m.start_byte, m.end_byte) for m in matches]

    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first struct/enum/impl/trait/mod)."""
        root = self._parse(content)
        for child in self._get_children(root):
            if child.type in RUST_BODY_TYPES:
                return self._byte_to_char(content, child.start_byte)
        return len(content)

    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
        """Extract the declaration name from a code region."""
        snippet = content[start:end]
        root = self._parse(snippet)
        for child in self._get_children(root):
            if child.type in RUST_DECL_TYPES:
                if child.type == 'impl_item':
                    return self._get_impl_type_name(child)
                name_node = child.child_by_field_name('name')
                if name_node:
                    return name_node.text.decode('utf-8')
        return None

    def get_insertion_point(self, content: str, target_path: str) -> Optional[int]:
        """Get the byte offset for inserting new content based on insertion anchor."""
        target = parse_target_path(target_path)
        if not target.is_insertion:
            return None
        items = self._build_item_index(content)
        if target.insertion_anchor == 'append_file':
            return len(content)
        elif target.insertion_anchor == 'append_module':
            if not target.module_path:
                return len(content)
            return len(content)
        elif target.insertion_anchor in ('insert_before', 'insert_after'):
            if target.insertion_ref:
                ref_target = parse_target_path(target.insertion_ref)
                matches = self._find_matches(items, ref_target)
                if matches:
                    if target.insertion_anchor == 'insert_before':
                        return matches[0].start_byte
                    else:
                        return matches[0].end_byte
        return None

    def get_impl_block_insertion_point(self, content: str, target_path: str) -> Optional[int]:
        """Get insertion point for a new method inside an impl block.
        
        When multiple impl blocks match and no @N selector is specified,
        defaults to the first matching block (since this is an insertion,
        not a replacement).
        """
        target = parse_target_path(target_path)
        if not target.is_impl_target or not target.associated_name:
            return None
        items = self._build_item_index(content)
        impl_target = TargetPath(
            module_path=target.module_path,
            impl_type=target.impl_type,
            impl_trait=target.impl_trait,
            attr_filter=target.attr_filter,
            occurrence=target.occurrence
        )
        matches = self._find_matches(items, impl_target)
        if len(matches) == 0:
            return None
        # For insertion, default to first match if no occurrence specified
        # (this is reasonable since we're adding a new method, not replacing)
        impl_item = matches[0]
        if impl_item.node:
            body = impl_item.node.child_by_field_name('body')
            if body is None:
                for child in impl_item.node.children:
                    if child.type == 'declaration_list':
                        body = child
                        break
            if body:
                return self._byte_to_char(content, body.end_byte - 1)
        return None

    def format_candidates_diagnostic(self, content: str, target_path: str, max_candidates: int = 10) -> str:
        """Generate a diagnostic message listing candidate matches."""
        items = self._build_item_index(content)
        lines = [f'No match found for target path: {target_path}', '', 'Candidates:']
        count = 0
        for item in items:
            if count >= max_candidates:
                lines.append(f'  ... and {len(items) - count} more')
                break
            path = item.canonical_path
            attrs = f' {item.attrs_fingerprint}' if item.attrs_fingerprint else ''
            lines.append(f'  [{item.kind}] {path}{attrs}')
            for assoc in item.associated_items[:3]:
                lines.append(f'    .{assoc.name}')
            if len(item.associated_items) > 3:
                lines.append(f'    ... and {len(item.associated_items) - 3} more methods')
            count += 1
        return '\n'.join(lines)

    def update_header(self, source: str, new_header: str) -> str:
        """Replace the header section of a Rust source file."""
        header_end = self.find_header_end(source)
        new_header_clean = new_header.strip() + '\n\n'
        return new_header_clean + source[header_end:]


# Register the handler
if TREE_SITTER_AVAILABLE:
    register_handler('.rs', RustHandler())
