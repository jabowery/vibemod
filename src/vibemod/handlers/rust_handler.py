# src/vibemod/handlers/rust_handler.py
"""
Rust language handler using tree-sitter-rust.

Implements the vibemod Rust specification:
- Extended target path grammar
- Multi-match replace/remove semantics  
- Explicit insertion anchors
- Signature-based disambiguation
- Rich error diagnostics
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Set

from .base import LanguageHandler, register_handler

try:
    import tree_sitter_rust as tsrust
    from tree_sitter import Language, Parser, Node
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


# =============================================================================
# RUST DECLARATION TYPES
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
# TARGET PATH PARSING
# =============================================================================

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
        if not TREE_SITTER_AVAILABLE:
            raise ImportError('tree-sitter and tree-sitter-rust are required for Rust support. Install with: pip install tree-sitter tree-sitter-rust')
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
        children = list(node.children)
        for child in children:
            if child.type == 'attribute_item':
                attr_text = content[child.start_byte:child.end_byte].strip()
                pending_attrs.append(attr_text)
                if attr_start is None:
                    attr_start = child.start_byte
                continue
            if child.type in RUST_DECL_TYPES:
                item = self._index_item(child, module_path, pending_attrs, attr_start if pending_attrs else child.start_byte, content)
                if item:
                    items.append(item)
                    if child.type in ('impl_item', 'trait_item'):
                        self._index_associated_items(child, item, content)
                pending_attrs = []
                attr_start = None
            elif child.type == 'mod_item':
                name_node = child.child_by_field_name('name')
                if name_node:
                    mod_name = name_node.text.decode('utf-8')
                    body = child.child_by_field_name('body')
                    if body:
                        self._index_scope(body, module_path + [mod_name], items, content)
                pending_attrs = []
                attr_start = None
            elif child.type not in ('line_comment', 'block_comment'):
                pending_attrs = []
                attr_start = None

    def _index_item(self, node: 'Node', module_path: List[str], attrs: List[str], start_byte: int, content: str) -> Optional[IndexedItem]:
        """Create an IndexedItem from a tree-sitter node."""
        item = IndexedItem(kind=node.type, name=None, module_path=list(module_path), start_byte=start_byte, end_byte=node.end_byte, attrs=list(attrs), attrs_fingerprint=' '.join(sorted(attrs)), node=node)
        if node.type == 'impl_item':
            item.impl_type = self._get_impl_type_name(node)
            item.impl_trait = self._get_impl_trait_name(node)
        else:
            name_node = node.child_by_field_name('name')
            if name_node:
                item.name = name_node.text.decode('utf-8')
        return item

    def _index_associated_items(self, impl_node: 'Node', parent_item: IndexedItem, content: str) -> None:
        """Index associated items within an impl or trait block."""
        body = impl_node.child_by_field_name('body')
        if body is None:
            for child in impl_node.children:
                if child.type == 'declaration_list':
                    body = child
                    break
        if body is None:
            return
        pending_attrs: List[str] = []
        attr_start: Optional[int] = None
        for child in body.children:
            if child.type == 'attribute_item':
                attr_text = content[child.start_byte:child.end_byte].strip()
                pending_attrs.append(attr_text)
                if attr_start is None:
                    attr_start = child.start_byte
                continue
            if child.type in RUST_ASSOCIATED_ITEM_TYPES:
                name_node = child.child_by_field_name('name')
                if name_node:
                    assoc_item = IndexedItem(kind=child.type, name=name_node.text.decode('utf-8'), module_path=parent_item.module_path, start_byte=attr_start if pending_attrs else child.start_byte, end_byte=child.end_byte, attrs=list(pending_attrs), attrs_fingerprint=' '.join(sorted(pending_attrs)), parent_impl=parent_item, node=child)
                    parent_item.associated_items.append(assoc_item)
                pending_attrs = []
                attr_start = None
            elif child.type not in ('line_comment', 'block_comment'):
                pending_attrs = []
                attr_start = None

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
        for child in root.children:
            if child.type in RUST_BODY_TYPES:
                return child.start_byte
        return len(content)

    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
        """Extract the declaration name from a code region."""
        snippet = content[start:end]
        root = self._parse(snippet)
        for child in root.children:
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
        
        Returns byte offset just before the closing brace of the impl block.
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
                return body.end_byte - 1
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