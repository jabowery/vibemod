# src/vibemod/handlers/rust_handler.py
"""Rust language handler using tree-sitter-rust."""

import re
from typing import Optional, Tuple, List

from .base import LanguageHandler, register_handler

try:
    import tree_sitter_rust as tsrust
    from tree_sitter import Language, Parser, Node
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


# Rust declaration node types that we consider "declarations"
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

# Types that mark the start of the "body" (not header)
RUST_BODY_TYPES = frozenset({
    'struct_item',
    'enum_item',
    'impl_item',
    'trait_item',
    'mod_item',
    'union_item',
})


class RustHandler(LanguageHandler):
    """Handler for Rust source files using tree-sitter-rust."""

    def __init__(self):
        if not TREE_SITTER_AVAILABLE:
            raise ImportError('tree-sitter and tree-sitter-rust are required for Rust support. Install with: pip install tree-sitter tree-sitter-rust')
        self._language = Language(tsrust.language())
        self._parser = Parser(self._language)

    def _parse(self, content: str) -> 'Node':
        """Parse Rust source and return the root node."""
        tree = self._parser.parse(content.encode('utf-8'))
        return tree.root_node

    def _get_node_name(self, node: 'Node') -> Optional[str]:
        """Extract the name from a declaration node."""
        name_node = node.child_by_field_name('name')
        if name_node:
            return name_node.text.decode('utf-8')
        if node.type == 'impl_item':
            type_node = node.child_by_field_name('type')
            if type_node:
                if type_node.type == 'type_identifier':
                    return type_node.text.decode('utf-8')
                elif type_node.type == 'generic_type':
                    ident = type_node.child_by_field_name('type')
                    if ident:
                        return ident.text.decode('utf-8')
        return None

    def _find_in_scope(self, node: 'Node', name: str, content_bytes: bytes) -> Optional['Node']:
        """Find a named declaration within a scope node."""
        if node.type == 'source_file':
            children = node.children
        elif node.type in ('impl_item', 'trait_item'):
            body = node.child_by_field_name('body')
            if body is None:
                for child in node.children:
                    if child.type == 'declaration_list':
                        body = child
                        break
            if body is None:
                return None
            children = body.children
        elif node.type == 'mod_item':
            body = node.child_by_field_name('body')
            if body is None:
                return None
            children = body.children
        else:
            children = node.children
        for child in children:
            if child.type in RUST_DECL_TYPES:
                child_name = self._get_node_name(child)
                if child_name == name:
                    return child
        return None

    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """Find a declaration using tree-sitter."""
        root = self._parse(content)
        content_bytes = content.encode('utf-8')
        parts = target_path.split('.')
        current_scope = root
        for i, part in enumerate(parts):
            found = self._find_in_scope(current_scope, part, content_bytes)
            if found is None:
                return None
            if i == len(parts) - 1:
                return (found.start_byte, found.end_byte)
            else:
                current_scope = found
        return None

    def find_header_end(self, content: str) -> int:
        """
        Find where the header ends in a Rust file.

        The header includes: use statements, extern crate, mod declarations (without body),
        top-level const/static, type aliases, and top-level functions.

        The "body" starts at the first struct, enum, impl, trait, or mod with body.
        """
        root = self._parse(content)
        first_body_start = len(content)
        for child in root.children:
            if child.type in RUST_BODY_TYPES:
                if child.start_byte < first_body_start:
                    first_body_start = child.start_byte
                break
        return first_body_start

    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
        """Extract the declaration name from a code region."""
        snippet = content[start:end]
        root = self._parse(snippet)
        for child in root.children:
            if child.type in RUST_DECL_TYPES:
                return self._get_node_name(child)
        return None
if TREE_SITTER_AVAILABLE:
    register_handler('.rs', RustHandler())