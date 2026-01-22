from dataclasses import dataclass
from typing import List, Tuple, Optional
import tree_sitter
import textwrap
import re
import os

LATEX_DECL_TYPES = ['section_command', 'environment', 'generic_command']  # Example node types from grammar
LATEX_ASSOCIATED_ITEM_TYPES = ['item']  # e.g., \item in lists

@dataclass
class IndexedItem:
    kind: str
    name: Optional[str]
    start_byte: int
    end_byte: int
    parent_impl: Optional['IndexedItem'] = None
    associated_items: List['IndexedItem'] = None  # type: ignore

    def __post_init__(self):
        if self.associated_items is None:
            self.associated_items = []

@dataclass
class TargetPath:
    chain: List[str]
    occurrence: Optional[int] = None
    insertion_anchor: Optional[str] = None
    insertion_ref: Optional[str] = None
    # Add LaTeX-specific fields if needed, e.g., is_environment

def parse_target_path(target_path: str) -> TargetPath:
    # Similar to Rust/Python parsers, adapt for LaTeX (e.g., 'begin.itemize.item@2')
    chain = target_path.split('.')
    occurrence = None
    insertion_anchor = None
    insertion_ref = None
    if '@' in chain[-1]:
        last, suffix = chain[-1].rsplit('@', 1)
        chain[-1] = last
        if suffix.isdigit():
            occurrence = int(suffix)
        elif suffix in ('append', 'append_file', 'append_preamble'):
            insertion_anchor = suffix
        elif suffix.startswith(('insert_before(', 'insert_after(')):
            anchor = 'insert_before' if 'before' in suffix else 'insert_after'
            open_par = suffix.find('(')
            close_par = suffix.rfind(')')
            insertion_anchor = anchor
            insertion_ref = suffix[open_par + 1:close_par]
    chain = [p for p in chain if p]
    return TargetPath(chain, occurrence, insertion_anchor, insertion_ref)

class LatexHandler:
    def __init__(self):
        self._language = tree_sitter.Language(os.path.join(os.path.dirname(__file__), 'tree-sitter-latex.so'))  # Path to compiled lib
        self._parser = tree_sitter.Parser()
        self._parser.set_language(self._language)

    def _parse(self, content: str) -> tree_sitter.Node:
        return self._parser.parse(content.encode('utf-8')).root_node

    def _byte_to_char(self, text: str, byte_offset: int) -> int:
        return len(text.encode('utf-8')[:byte_offset].decode('utf-8'))

    def _build_item_index(self, content: str) -> List[IndexedItem]:
        root = self._parse(content)
        items: List[IndexedItem] = []
        self._index_scope(root, items, content)
        return items

    def _index_scope(self, node: tree_sitter.Node, items: List[IndexedItem], content: str) -> None:
        for child in node.children:
            if child.type in LATEX_DECL_TYPES:
                name = self._get_name(child)
                start_char = self._byte_to_char(content, child.start_byte)
                end_char = self._byte_to_char(content, child.end_byte)
                item = IndexedItem(kind=child.type, name=name, start_byte=start_char, end_byte=end_char)
                items.append(item)
                if child.type == 'environment':
                    self._index_associated_items(child, item, content)
            elif child.type == 'group':  # Or other scoping nodes
                self._index_scope(child, items, content)

    def _index_associated_items(self, env_node: tree_sitter.Node, parent_item: IndexedItem, content: str) -> None:
        body = env_node.named_child(1)  # Assuming structure: begin, body, end
        if body:
            for child in body.children:
                if child.type in LATEX_ASSOCIATED_ITEM_TYPES:
                    name = self._get_name(child)
                    start_char = self._byte_to_char(content, child.start_byte)
                    end_char = self._byte_to_char(content, child.end_byte)
                    assoc_item = IndexedItem(kind=child.type, name=name, start_byte=start_char, end_byte=end_char, parent_impl=parent_item)
                    parent_item.associated_items.append(assoc_item)

    def _get_name(self, node: tree_sitter.Node) -> Optional[str]:
        # Extract name, e.g., for \section{Title} -> 'section'; for \begin{itemize} -> 'itemize'
        if node.type == 'generic_command':
            name_node = node.child_by_field_name('name')
            return name_node.text.decode('utf-8') if name_node else None
        elif node.type == 'environment':
            name_node = node.named_child(0).named_child(0)  # \begin{name}
            return name_node.text.decode('utf-8') if name_node else None
        return None

    def _find_matches(self, items: List[IndexedItem], target: TargetPath) -> List[IndexedItem]:
        candidates = [item for item in items if self._item_matches(item, target)]
        if target.occurrence is not None:
            if 1 <= target.occurrence <= len(candidates):
                return [candidates[target.occurrence - 1]]
            return []
        return candidates

    def _item_matches(self, item: IndexedItem, target: TargetPath) -> bool:
        # Adapt for LaTeX: match chain like 'begin.document.section'
        # Simplified; expand for nested chains
        if target.chain and item.name != target.chain[-1]:
            return False
        if len(target.chain) > 1 and item.parent_impl and item.parent_impl.name != target.chain[-2]:
            return False
        return True

    def find_all_declarations(self, content: str, target_path: str) -> List[Tuple[int, int]]:
        target = parse_target_path(target_path)
        items = self._build_item_index(content)
        matches = self._find_matches(items, target)
        return [(m.start_byte, m.end_byte) for m in matches]

    def find_header_end(self, content: str) -> int:
        root = self._parse(content)
        for child in root.children:
            if child.type == 'environment' and self._get_name(child) == 'document':
                return self._byte_to_char(content, child.named_child(0).end_byte)  # After \begin{document}
        return 0

    def validate_syntax(self, content: str, original_content: Optional[str] = None) -> Optional[str]:
        try:
            self._parse(content)
            return None
        except Exception as e:
            return str(e)

    def validate_single_declaration(self, content: str) -> Optional[str]:
        root = self._parse(content)
        if len([c for c in root.children if c.type in LATEX_DECL_TYPES]) != 1:
            return 'Content must be a single LaTeX declaration'
        return None

    def modify_declaration(self, source: str, dotted_target: str, content: Optional[str], remove: bool):
        if remove:
            spans = self.find_all_declarations(source, dotted_target)
            if not spans:
                return
            spans.sort(key=lambda s: s[0], reverse=True)
            new_source = source
            for start, end in spans:
                before = new_source[:start].rstrip()
                after = new_source[end:].lstrip()
                new_source = before + '\n' + after if after else before
            new_source = re.sub(r'\n{3,}', '\n\n', new_source)
        else:
            if content is None:
                raise ValueError('Content required')
            content = textwrap.dedent(content).strip()
            single_decl_error = self.validate_single_declaration(content)
            if single_decl_error:
                raise ValueError(f'Invalid content: {single_decl_error}')
            target = parse_target_path(dotted_target)
            if target.insertion_anchor:
                insertion_point = self.get_insertion_point(source, dotted_target)
                if insertion_point is None:
                    raise ValueError(f'No insertion point for {dotted_target}')
                before = source[:insertion_point].rstrip()
                after = source[insertion_point:].lstrip()
                new_source = before + '\n' + content + '\n' + after
                new_source = re.sub(r'\n{3,}', '\n\n', new_source)
            else:
                spans = self.find_all_declarations(source, dotted_target)
                if not spans:
                    new_source = source.rstrip() + '\n' + content + '\n'
                else:
                    spans.sort(key=lambda s: s[0], reverse=True)
                    new_source = source
                    for start, end in spans:
                        new_source = new_source[:start] + content + new_source[end:]
        syntax_error = self.validate_syntax(new_source)
        if syntax_error:
            raise ValueError(f'Invalid syntax after mod: {syntax_error}')
        with open(file_path, 'w', encoding='utf-8') as f:  # Note: file_path from outer scope; adjust
            f.write(new_source)

    def get_insertion_point(self, content: str, target_path: str) -> Optional[int]:
        target = parse_target_path(target_path)
        if target.insertion_anchor == 'append_file':
            return len(content)
        elif target.insertion_anchor == 'append_preamble':
            return self.find_header_end(content)
        # Implement other anchors similarly (e.g., insert_before ref spans)

# Register if TREE_SITTER_AVAILABLE
from .base import TREE_SITTER_AVAILABLE, register_handler
if TREE_SITTER_AVAILABLE:
    register_handler('.tex', LatexHandler())

