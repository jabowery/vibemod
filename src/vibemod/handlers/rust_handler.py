# src/vibemod/handlers/rust_handler.py
"""
Rust language handler using tree-sitter-rust.

Implements the vibemod Rust specification:
- Extended target path grammar
- Multi-match replace/remove semantics  
- Insertion anchors
- Signature-based disambiguation
- Rich error diagnostics
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
    # Note: use_declaration is NOT included here because you can have multiple
    # use statements importing different items with the same final name from
    # different modules, e.g.:
    #   use foo::Thing;
    #   use bar::Thing as BarThing;
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


@dataclass
class TargetPath:
    """Parsed representation of a vibemod target path for Rust."""
    module_path: List[str] = field(default_factory=list)
    item_name: Optional[str] = None
    impl_type: Optional[str] = None
    impl_trait: Optional[str] = None
    associated_name: Optional[str] = None
    attr_filter: Optional[str] = None
    occurrence: Optional[int] = None
    insertion_anchor: Optional[str] = None
    insertion_ref: Optional[str] = None

    @property
    def is_impl_target(self) -> bool:
        return self.impl_type is not None

    @property
    def is_trait_impl(self) -> bool:
        return self.impl_trait is not None

    @property
    def is_insertion(self) -> bool:
        return self.insertion_anchor is not None

def parse_target_path(target_path: str) -> TargetPath:
    """
    Parse a vibemod target path string into structured form.
    
    Examples:
        "TLinda" -> item_name="TLinda"
        "impl(TLinda)" -> impl_type="TLinda"
        "impl(TLinda).eval" -> impl_type="TLinda", associated_name="eval"
        "impl(Display for TLinda).fmt" -> impl_trait="Display", impl_type="TLinda", associated_name="fmt"
        "impl(TLinda)#[cfg(test)].debug_count" -> impl_type="TLinda", attr_filter="#[cfg(test)]", associated_name="debug_count"
        "impl(TLinda)@2" -> impl_type="TLinda", occurrence=2
        "foo::bar::Baz" -> module_path=["foo", "bar"], item_name="Baz"
        "TLinda@append_file" -> item_name="TLinda", insertion_anchor="append_file"
    """
    result = TargetPath()
    remaining = target_path.strip()
    insertion_match = re.search('@(append_module|append_file|insert_before\\([^)]+\\)|insert_after\\([^)]+\\))$', remaining)
    if insertion_match:
        anchor = insertion_match.group(1)
        if anchor.startswith('insert_before('):
            result.insertion_anchor = 'insert_before'
            result.insertion_ref = anchor[14:-1]
        elif anchor.startswith('insert_after('):
            result.insertion_anchor = 'insert_after'
            result.insertion_ref = anchor[13:-1]
        else:
            result.insertion_anchor = anchor
        remaining = remaining[:insertion_match.start()]
    occurrence_match = re.search('@(\\d+)$', remaining)
    if occurrence_match:
        result.occurrence = int(occurrence_match.group(1))
        remaining = remaining[:occurrence_match.start()]
    attr_match = re.search('(#\\[[^\\]]+\\])', remaining)
    if attr_match:
        result.attr_filter = attr_match.group(1)
        remaining = remaining[:attr_match.start()] + remaining[attr_match.end():]
    impl_match = re.match('^(.+::)?impl\\(([^)]+)\\)(?:\\.(\\w+))?$', remaining)
    if impl_match:
        if impl_match.group(1):
            result.module_path = impl_match.group(1).rstrip('::').split('::')
        impl_spec = impl_match.group(2).strip()
        trait_match = re.match('(\\w+(?:::\\w+)*)\\s+for\\s+(\\w+(?:::\\w+)*)', impl_spec)
        if trait_match:
            result.impl_trait = trait_match.group(1)
            result.impl_type = trait_match.group(2)
        else:
            result.impl_type = impl_spec
        if impl_match.group(3):
            result.associated_name = impl_match.group(3)
        return result
    if '.' in remaining and 'impl(' not in remaining:
        parts = remaining.split('.')
        type_part = parts[0]
        if '::' in type_part:
            segments = type_part.split('::')
            result.module_path = segments[:-1]
            result.impl_type = segments[-1]
        else:
            result.impl_type = type_part
        result.associated_name = parts[1]
        return result
    if '::' in remaining:
        segments = remaining.split('::')
        result.module_path = segments[:-1]
        result.item_name = segments[-1]
    else:
        result.item_name = remaining
    return result

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

class RustHandler(LanguageHandler):
    """
    Handler for Rust source files using tree-sitter-rust.
    
    Implements the full vibemod Rust specification with:
    - Extended target path grammar
    - Multi-match semantics
    - Insertion anchors
    - Signature-based disambiguation
    - Rich error diagnostics
    """

    def _get_declaration_name(self, node: 'Node', content: str) -> Optional[str]:
        """Extract the name from a declaration node.

    Handles all Rust declaration types:
    - function_item, struct_item, enum_item, trait_item, mod_item, 
      type_item, const_item, static_item, union_item, macro_definition
    - impl_item (returns the type name)
    - use_declaration (returns the imported name)
    """
        if node.type == 'impl_item':
            return self._get_impl_type_name(node)
        if node.type == 'use_declaration':
            return self._get_use_declaration_name(node)
        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')
        return None

    def modify_declaration(self, file_path: str, source: str, target_path: str, content: Optional[str], remove: bool, debug_dump_func=None) -> str:
        """
    Rust-specific declaration modification.

    Extends base class to handle:
    - impl block method insertion
    - Rust-specific target path parsing (impl:Type.method)
    """
        source_before = source
        target = parse_target_path(target_path)

        def validate_and_return(new_source: str) -> str:
            syntax_error = self.validate_syntax(new_source, original_content=source_before)
            if syntax_error:
                if debug_dump_func:
                    debug_dir = debug_dump_func(file_path=file_path, target_path=target_path, content=content, source_before=source_before, source_after=new_source, error_message=syntax_error, remove=remove)
                    raise ValueError(f'Modification would create syntactically invalid Rust code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
                raise ValueError(f'Modification would create syntactically invalid Rust code:\n{syntax_error}')
            dup_error = self.validate_no_illegal_duplicates(new_source)
            if dup_error:
                raise ValueError(f'Modification would create invalid Rust code:\n{dup_error}')
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
            new_source = re.sub('\\n{3,}', '\n\n', new_source)
            return validate_and_return(new_source)
        if content is None:
            raise ValueError('Content required for declare operation')
        content = textwrap.dedent(content).strip()
        if target.is_impl_target and target.associated_name:
            unwrapped = self.unwrap_method_from_impl(content, target.associated_name)
            if unwrapped is not None:
                content = unwrapped
        single_decl_error = self.validate_single_declaration(content)
        if single_decl_error:
            if debug_dump_func:
                debug_dir = debug_dump_func(file_path=file_path, target_path=target_path, content=content, source_before=source_before, source_after=content, error_message=single_decl_error, remove=remove)
                raise ValueError(f'Invalid declare content:\n{single_decl_error}\n\nDebug files written to: {debug_dir}')
            raise ValueError(f'Invalid declare content:\n{single_decl_error}')
        if target.is_insertion:
            insertion_point = self.get_insertion_point(source, target_path)
            if insertion_point is None:
                diagnostic = self.format_candidates_diagnostic(source, target_path)
                raise ValueError(f'Cannot determine insertion point.\n{diagnostic}')
            before = source[:insertion_point].rstrip()
            after = source[insertion_point:].lstrip()
            new_source = before + '\n\n' + content + '\n\n' + after
            new_source = re.sub('\\n{3,}', '\n\n', new_source)
            return validate_and_return(new_source)
        if target.is_impl_target and target.associated_name:
            spans = self.find_all_declarations(source, target_path)
            if spans:
                adjusted_spans = [self.adjust_span_for_attributes(source, s, e, content) for s, e in spans]
                adjusted_spans.sort(key=lambda s: s[0], reverse=True)
                new_source = source
                for start, end in adjusted_spans:
                    new_source = new_source[:start] + content + new_source[end:]
                return validate_and_return(new_source)
            insertion_point = self.get_impl_block_insertion_point(source, target_path)
            if insertion_point is None:
                diagnostic = self.format_candidates_diagnostic(source, target_path)
                raise ValueError(f"Cannot insert method '{target.associated_name}': no matching impl block found or multiple impl blocks match (use @N selector).\n{diagnostic}")
            line_start = source.rfind('\n', 0, insertion_point) + 1
            line_content = source[line_start:insertion_point]
            base_indent = len(line_content) - len(line_content.lstrip())
            indent = ' ' * (base_indent + 4)
            indented_content = '\n'.join((indent + line if line.strip() else line for line in content.split('\n')))
            new_source = source[:insertion_point] + '\n' + indented_content + '\n' + source[insertion_point:]
            return validate_and_return(new_source)
        spans = self.find_all_declarations(source, target_path)
        if spans:
            adjusted_spans = [self.adjust_span_for_attributes(source, s, e, content) for s, e in spans]
            adjusted_spans.sort(key=lambda s: s[0], reverse=True)
            new_source = source
            for start, end in adjusted_spans:
                new_source = new_source[:start] + content + new_source[end:]
            return validate_and_return(new_source)
        if target.is_impl_target and (not target.associated_name):
            diagnostic = self.format_candidates_diagnostic(source, target_path)
            raise ValueError(f"No impl block found for '{target_path}'. To add a new impl block, use an insertion anchor like @append_file.\n{diagnostic}")
        new_source = source.rstrip() + '\n\n' + content + '\n' if source.strip() else content + '\n'
        return validate_and_return(new_source)

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
        return stripped.startswith('#[') or stripped.startswith('///') or stripped.startswith('//!') or stripped.startswith('/**') or stripped.startswith('/*!')

    def is_insertion_target(self, target_path: str) -> bool:
        """Check if target path is an insertion anchor."""
        target = parse_target_path(target_path)
        return target.is_insertion

    def get_decl_types(self) -> frozenset:
        """Return Rust declaration node types."""
        return RUST_DECL_TYPES

    def _get_use_declaration_name(self, node: 'Node') -> Optional[str]:
        """Extract the imported name from a use_declaration node.

    Handles various use patterns:
    - `use foo::Bar;` -> "Bar"
    - `use foo::Bar as Baz;` -> "Baz" (the alias)
    - `pub use kernel::KernelDiag;` -> "KernelDiag"
    - `use foo::{A, B};` -> None (use_list not supported as single target)
    - `use foo::*;` -> None (glob imports not supported as single target)
    """
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
        elif arg.type == 'scoped_use_list':
            return None
        elif arg.type == 'use_wildcard':
            return None
        return None

    def unwrap_method_from_impl(self, content: str, expected_method: str) -> Optional[str]:
        """
    If content is an impl block containing a single method matching expected_method,
    extract and return just the method. Otherwise return None.

    This allows users to write:
        impl Type {
            fn method(&self) { ... }
        }
    when declaring impl:Type.method, and have it do the right thing.
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

    def _byte_to_char(self, content: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to Python character offset.

    Tree-sitter returns byte offsets, but Python string indexing uses
    character offsets. For files with multi-byte UTF-8 characters,
    these differ. This method converts byte offsets to character offsets.
    """
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

    def validate_single_declaration(self, content: str) -> Optional[str]:
        """
    Validate that content contains exactly one top-level declaration.

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
            elif child.type == 'attribute_item':
                continue
            elif child.type in ('line_comment', 'block_comment'):
                continue
            elif child.type == 'ERROR':
                return f'Declare content has syntax errors and cannot be parsed.'
        if len(declarations) == 0:
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
                else:
                    decl_names.append(d.type.replace('_item', '').replace('_declaration', ''))
            return f"Declare content contains {len(declarations)} declarations, but only one is allowed per directive.\nFound: {', '.join(decl_names)}\nSplit these into separate MMM declare MMM blocks."
        return None

    def _collect_errors(self, node: 'Node', content: str, errors: List=None) -> List[tuple]:
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

    def validate_syntax(self, content: str, original_content: str=None) -> Optional[str]:
        """
    Validate Rust syntax using tree-sitter.

    Returns None if valid, or an error message if invalid.

    The original_content parameter is accepted for API compatibility but
    is no longer used now that we use tree-sitter-rust-orchard which
    correctly parses modern Rust syntax.
    """
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
        """
    Validate that the content has no illegal duplicate declarations.

    Returns None if valid, or an error message describing the duplicates.
    """
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

    def __init__(self):
        import tree_sitter_rust_orchard as tsrust
        from tree_sitter import Language, Parser
        self._language = Language(tsrust.language())
        self._parser = Parser(self._language)

    def _parse(self, content: str) -> 'Node':
        """Parse Rust source and return the root node."""
        tree = self._parser.parse(content.encode('utf-8'))
        return tree.root_node

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
        """Create an IndexedItem from a tree-sitter node.

    Note: start_char and end_char are character offsets (not byte offsets).
    The IndexedItem fields are still named start_byte/end_byte for compatibility,
    but they now store character offsets.
    """
        item = IndexedItem(kind=node.type, name=None, module_path=list(module_path), start_byte=start_char, end_byte=end_char, attrs=list(attrs), attrs_fingerprint=' '.join(sorted(attrs)), node=node)
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
                    assoc_item = IndexedItem(kind=child.type, name=name_node.text.decode('utf-8'), module_path=parent_item.module_path, start_byte=start_char, end_byte=end_char, attrs=list(pending_attrs), attrs_fingerprint=' '.join(sorted(pending_attrs)), parent_impl=parent_item, node=child)
                    parent_item.associated_items.append(assoc_item)
                pending_attrs = []
                attr_start = None
                doc_comment_start = None
            elif child.type not in ('line_comment', 'block_comment'):
                pending_attrs = []
                attr_start = None
                doc_comment_start = None

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

    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """
    Find declaration(s) matching the target path.

    Returns the span of the FIRST match. For multi-match operations,
    use find_all_declarations().
    """
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
        """
        Get the byte offset for inserting new content based on insertion anchor.
        
        Returns None if no valid insertion point can be determined.
        """
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
        """
    Get insertion point for a new method inside an impl block.

    Returns character offset just before the closing brace of the impl block.
    """
        target = parse_target_path(target_path)
        if not target.is_impl_target or not target.associated_name:
            return None
        items = self._build_item_index(content)
        impl_target = TargetPath(module_path=target.module_path, impl_type=target.impl_type, impl_trait=target.impl_trait, attr_filter=target.attr_filter, occurrence=target.occurrence)
        matches = self._find_matches(items, impl_target)
        if len(matches) == 0:
            return None
        if len(matches) > 1 and target.occurrence is None:
            return None
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

    def format_candidates_diagnostic(self, content: str, target_path: str, max_candidates: int=10) -> str:
        """
        Generate a diagnostic message listing candidate matches.
        
        Used for error reporting when no match or ambiguous match.
        """
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
if TREE_SITTER_AVAILABLE:
    register_handler('.rs', RustHandler())
