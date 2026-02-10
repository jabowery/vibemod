# src/vibemod/handlers/elixir_handler.py
"""Elixir language handler for vibemod."""

import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Any, Set

from tree_sitter_language_pack import get_language, get_parser

from .base import LanguageHandler, register_handler

# Get Elixir language and parser from language pack
ELIXIR_LANGUAGE = get_language("elixir")

# Elixir declaration types - these are actually 'call' nodes with specific identifiers
ELIXIR_DEF_KEYWORDS = frozenset({
    'defmodule', 'def', 'defp', 'defmacro', 'defmacrop', 
    'defguard', 'defguardp', 'defdelegate', 'defstruct',
    'defprotocol', 'defimpl', 'defexception', 'defoverridable',
})

# Module-level constructs
ELIXIR_MODULE_KEYWORDS = frozenset({
    'defmodule', 'defprotocol', 'defimpl',
})


@dataclass
class IndexedItem:
    """An indexed declaration in Elixir source."""
    kind: str  # 'defmodule', 'def', 'defp', etc.
    name: Optional[str]
    module_path: List[str]  # Enclosing module names
    start_byte: int  # Character offset (despite the name)
    end_byte: int    # Character offset
    arity: Optional[int] = None  # For functions
    attrs: List[str] = field(default_factory=list)  # Module attributes before this
    associated_items: List['IndexedItem'] = field(default_factory=list)
    parent_module: Optional['IndexedItem'] = None
    
    def matches_attr_filter(self, attr_filter: Optional[str]) -> bool:
        if attr_filter is None:
            return True
        return attr_filter in self.attrs


@dataclass 
class TargetPath:
    """Parsed target path for Elixir declarations."""
    module_path: List[str] = field(default_factory=list)  # e.g., ['MyApp', 'Users']
    item_name: Optional[str] = None  # Function/macro name
    arity: Optional[int] = None  # Function arity
    is_private: bool = False  # defp vs def
    occurrence: Optional[int] = None  # @N occurrence selector
    
    @property
    def is_module_target(self) -> bool:
        return bool(self.module_path) and self.item_name is None
    
    @property
    def is_function_target(self) -> bool:
        return self.item_name is not None


class ElixirHandler(LanguageHandler):
    """Handler for Elixir source files."""
    
    def __init__(self):
        self._parser = get_parser("elixir")
    
    # =========================================================================
    # CORE PARSING
    # =========================================================================
    
    def _parse(self, content: str) -> Any:
        """Parse Elixir source and return root node."""
        return self._parser.parse(content.encode('utf-8')).root_node
    
    def _byte_to_char(self, content: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to character offset."""
        return len(content.encode('utf-8')[:byte_offset].decode('utf-8'))
    
    def _get_children(self, node: Any) -> List[Any]:
        """Get children of a node."""
        return list(node.children)
    
    def _get_declaration_name(self, node: Any, content: str) -> Optional[str]:
        """Extract name from a declaration node."""
        if node.type != 'call':
            return None
        
        # Get the keyword (def, defmodule, etc.)
        keyword = self._get_call_keyword(node)
        if keyword not in ELIXIR_DEF_KEYWORDS:
            return None
        
        # Get the name from arguments
        return self._extract_def_name(node, keyword)
    
    def _get_call_keyword(self, node: Any) -> Optional[str]:
        """Get the keyword from a call node (def, defmodule, etc.)."""
        for child in self._get_children(node):
            if child.type == 'identifier':
                return child.text.decode('utf-8')
        return None
    
    def _extract_def_name(self, node: Any, keyword: str) -> Optional[str]:
        """Extract the name from a def/defmodule call."""
        args = None
        for child in self._get_children(node):
            if child.type == 'arguments':
                args = child
                break
        
        if args is None:
            return None
        
        if keyword == 'defmodule' or keyword in ('defprotocol', 'defimpl'):
            # First arg is the module alias (could be MyApp.Users)
            for child in self._get_children(args):
                if child.type == 'alias':
                    return child.text.decode('utf-8')
                elif child.type == 'dot':
                    # Nested module like MyApp.Users
                    return child.text.decode('utf-8')
            return None
        
        # For def/defp/defmacro, first arg is the function call or name
        for child in self._get_children(args):
            if child.type == 'identifier':
                return child.text.decode('utf-8')
            elif child.type == 'call':
                # def hello(name) - the function name is a call
                for subchild in self._get_children(child):
                    if subchild.type == 'identifier':
                        return subchild.text.decode('utf-8')
            elif child.type == 'binary_operator':
                # Pattern matching heads like def foo(x) when is_integer(x)
                left = child.child_by_field_name('left')
                if left and left.type == 'call':
                    for subchild in self._get_children(left):
                        if subchild.type == 'identifier':
                            return subchild.text.decode('utf-8')
        
        return None
    
    def _get_module_full_path(self, item: 'IndexedItem') -> List[str]:
        """Get the full module path for an item, including nested module names."""
        if item.kind in ELIXIR_MODULE_KEYWORDS and item.name:
            # Module name might be "MyApp.Users" - split it
            parts = item.name.split('.')
            return item.module_path + parts
        return item.module_path
    
    def _extract_function_arity(self, node: Any) -> Optional[int]:
        """Extract arity from a function definition."""
        args = None
        for child in self._get_children(node):
            if child.type == 'arguments':
                args = child
                break
        
        if args is None:
            return 0
        
        # Find the function call within arguments
        for child in self._get_children(args):
            if child.type == 'call':
                # Count arguments in the function call
                call_args = None
                for subchild in self._get_children(child):
                    if subchild.type == 'arguments':
                        call_args = subchild
                        break
                
                if call_args is None:
                    return 0
                
                # Count actual arguments (skip commas, etc.)
                count = 0
                for subchild in self._get_children(call_args):
                    if subchild.type not in ('(', ')', ','):
                        count += 1
                return count
            elif child.type == 'identifier':
                # def foo - no args
                return 0
        
        return 0
    
    # =========================================================================
    # TARGET PATH PARSING
    # =========================================================================
    
    def parse_target_path(self, target_path: str) -> TargetPath:
        """
        Parse an Elixir target path.
        
        Supported formats:
        - MyModule                    -> module
        - MyModule.SubModule          -> nested module
        - MyModule.function_name      -> function in module
        - MyModule.function_name/2    -> function with arity
        - function_name               -> top-level function (rare)
        - defp:function_name          -> private function
        - MyModule.defp:func          -> private function in module
        - target@N                    -> Nth occurrence
        """
        result = TargetPath()
        path = target_path.strip()
        
        # Check for occurrence selector @N
        occ_match = re.search(r'@(\d+)$', path)
        if occ_match:
            result.occurrence = int(occ_match.group(1))
            path = path[:occ_match.start()]
        
        # Check for arity /N
        arity_match = re.search(r'/(\d+)$', path)
        if arity_match:
            result.arity = int(arity_match.group(1))
            path = path[:arity_match.start()]
        
        # Check for defp: prefix (private function)
        if path.startswith('defp:'):
            result.is_private = True
            path = path[5:]
        
        # Split by dots
        parts = path.split('.')
        
        # Determine what's module vs function
        # Convention: CamelCase = module, snake_case = function
        module_parts = []
        func_name = None
        
        for i, part in enumerate(parts):
            # Check for defp: prefix in the middle
            if part.startswith('defp:'):
                result.is_private = True
                func_name = part[5:]
                break
            elif part[0].isupper() if part else False:
                # Starts with uppercase - module name
                module_parts.append(part)
            else:
                # Starts with lowercase - function name
                func_name = part
                break
        
        result.module_path = module_parts
        result.item_name = func_name
        
        return result
    
    # =========================================================================
    # INDEXING
    # =========================================================================
    
    def _build_item_index(self, content: str) -> List[IndexedItem]:
        """Build a complete index of all declarations in the file."""
        root = self._parse(content)
        items: List[IndexedItem] = []
        self._index_scope(root, [], items, content, None)
        return items
    
    def _index_scope(
        self, 
        node: Any, 
        module_path: List[str], 
        items: List[IndexedItem], 
        content: str,
        parent_module: Optional[IndexedItem]
    ) -> None:
        """Recursively index declarations in a scope."""
        pending_attrs: List[str] = []
        attr_start: Optional[int] = None
        
        # Module-level attributes that should not be attached to functions
        MODULE_LEVEL_ATTRS = {'@moduledoc', '@behaviour', '@callback', '@type', '@typep', '@opaque', '@spec'}
        
        children = self._get_children(node)
        
        for child in children:
            # Collect module attributes
            if child.type == 'unary_operator':
                # Check if it's @ attribute
                for subchild in self._get_children(child):
                    if subchild.type == '@':
                        start_char = self._byte_to_char(content, child.start_byte)
                        end_char = self._byte_to_char(content, child.end_byte)
                        attr_text = content[start_char:end_char].strip()
                        
                        # Check if this is a module-level attribute
                        attr_name = attr_text.split()[0] if attr_text else ''
                        if attr_name in MODULE_LEVEL_ATTRS:
                            # Don't attach to next function, clear pending
                            pending_attrs = []
                            attr_start = None
                        else:
                            pending_attrs.append(attr_text)
                            if attr_start is None:
                                attr_start = start_char
                        break
                continue
            
            # Check for definition calls
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                
                if keyword in ELIXIR_DEF_KEYWORDS:
                    if pending_attrs:
                        start_char = attr_start
                    else:
                        start_char = self._byte_to_char(content, child.start_byte)
                    end_char = self._byte_to_char(content, child.end_byte)
                    
                    name = self._extract_def_name(child, keyword)
                    arity = None
                    if keyword in ('def', 'defp', 'defmacro', 'defmacrop', 'defguard', 'defguardp'):
                        arity = self._extract_function_arity(child)
                    
                    item = IndexedItem(
                        kind=keyword,
                        name=name,
                        module_path=list(module_path),
                        start_byte=start_char,
                        end_byte=end_char,
                        arity=arity,
                        attrs=list(pending_attrs),
                        parent_module=parent_module
                    )
                    items.append(item)
                    
                    # If it's a module, index its contents
                    if keyword in ELIXIR_MODULE_KEYWORDS:
                        do_block = self._find_do_block(child)
                        if do_block and name:
                            # Module name might be "MyApp.Users" - split it for the path
                            name_parts = name.split('.')
                            new_path = module_path + name_parts
                            self._index_scope(do_block, new_path, items, content, item)
                    
                    pending_attrs = []
                    attr_start = None
                else:
                    # Not a definition - clear pending attrs
                    pending_attrs = []
                    attr_start = None
            
            # Recurse into do_block for module-level
            elif child.type == 'do_block' and not module_path:
                self._index_scope(child, module_path, items, content, parent_module)
            
            # Clear attrs for non-attr, non-def nodes
            elif child.type not in ('unary_operator',):
                pending_attrs = []
                attr_start = None
    
    def _find_do_block(self, node: Any) -> Optional[Any]:
        """Find the do_block child of a node."""
        for child in self._get_children(node):
            if child.type == 'do_block':
                return child
        return None
    
    # =========================================================================
    # DECLARATION FINDING
    # =========================================================================
    
    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """Find the span of a declaration by target path."""
        target = self.parse_target_path(target_path)
        items = self._build_item_index(content)
        matches = self._find_matches(items, target)
        
        if not matches:
            return None
        
        return (matches[0].start_byte, matches[0].end_byte)
    
    def find_all_declarations(self, content: str, target_path: str) -> List[Tuple[int, int]]:
        """Find all declarations matching the target path."""
        target = self.parse_target_path(target_path)
        items = self._build_item_index(content)
        matches = self._find_matches(items, target)
        
        return [(m.start_byte, m.end_byte) for m in matches]
    
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
        
        # Module target - match the module itself
        if target.is_module_target:
            if item.kind not in ELIXIR_MODULE_KEYWORDS:
                return False
            
            # Get the full path of this module
            # item.name might be "MyApp.Users", module_path is parent
            if item.name:
                full_path = item.module_path + item.name.split('.')
            else:
                full_path = item.module_path
            
            if full_path != target.module_path:
                return False
            
            return True
        
        # Function target
        if target.is_function_target:
            if item.name != target.item_name:
                return False
            
            # Check module path
            if target.module_path:
                if item.module_path != target.module_path:
                    return False
            
            # Check private/public
            if target.is_private:
                if item.kind not in ('defp', 'defmacrop', 'defguardp'):
                    return False
            
            # Check arity if specified
            if target.arity is not None:
                if item.arity != target.arity:
                    return False
            
            return True
        
        return False
        if target.is_function_target:
            if item.name != target.item_name:
                return False
            
            # Check private/public
            if target.is_private:
                if item.kind not in ('defp', 'defmacrop', 'defguardp'):
                    return False
            
            # Check arity if specified
            if target.arity is not None:
                if item.arity != target.arity:
                    return False
            
            return True
        
        return False
    
    # =========================================================================
    # HEADER AND STRUCTURE
    # =========================================================================
    
    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first defmodule or function)."""
        root = self._parse(content)
        
        for child in self._get_children(root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword in ELIXIR_DEF_KEYWORDS:
                    return self._byte_to_char(content, child.start_byte)
        
        return len(content)
    
    def get_decl_types(self) -> frozenset:
        """Return declaration types (for Elixir, we check call keywords)."""
        return frozenset({'call'})  # We filter by keyword in other methods
    
    # =========================================================================
    # VALIDATION
    # =========================================================================
    
    def validate_single_declaration(self, content: str) -> Optional[str]:
        """Validate content contains exactly one top-level declaration."""
        root = self._parse(content)
        declarations = []
        
        for child in self._get_children(root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword in ELIXIR_DEF_KEYWORDS:
                    declarations.append((keyword, self._extract_def_name(child, keyword)))
            elif child.type == 'ERROR':
                return 'Declare content has syntax errors and cannot be parsed.'
        
        if len(declarations) == 0:
            return 'Declare content contains no valid Elixir declaration. Expected: defmodule, def, defp, defmacro, etc.'
        
        if len(declarations) == 1:
            return None
        
        # Multiple top-level declarations - might be OK for defmodule with multiple functions
        # But multiple defmodules is not OK
        module_count = sum(1 for k, _ in declarations if k in ELIXIR_MODULE_KEYWORDS)
        if module_count > 1:
            names = [f"{k} {n}" for k, n in declarations]
            return f"Declare content contains {len(declarations)} declarations: {', '.join(names)}.\nSplit these into separate MMM declare MMM blocks."
        
        return None
    
    def validate_syntax(self, content: str, original_content: str = None) -> Optional[str]:
        """Validate Elixir syntax."""
        root = self._parse(content)
        errors = self._collect_errors(root, content)
        
        if not errors:
            return None
        
        # If we have original content, check if errors are pre-existing
        if original_content:
            orig_root = self._parse(original_content)
            orig_errors = self._collect_errors(orig_root, original_content)
            if len(errors) <= len(orig_errors):
                return None
        
        error_msgs = []
        for error_text, line_num, col, context in errors[:5]:
            error_msgs.append(f"  Line {line_num}, column {col}: {error_text}")
            error_msgs.append(f"    Context: {context}")
        
        return "Syntax errors detected in resulting code:\n" + "\n".join(error_msgs) + \
               "\n\nThe modification has been rejected to prevent invalid code."
    
    def _collect_errors(self, node: Any, content: str, errors: List = None) -> List[tuple]:
        """Recursively collect ERROR nodes from parse tree."""
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
    
    def validate_no_illegal_duplicates(self, content: str) -> Optional[str]:
        """Check for illegal duplicate declarations."""
        items = self._build_item_index(content)
        
        # Track modules by full path
        modules: dict = {}  # full_path -> list of byte positions
        
        for item in items:
            if item.kind in ELIXIR_MODULE_KEYWORDS:
                full_path = '.'.join(item.module_path + ([item.name] if item.name else []))
                if full_path not in modules:
                    modules[full_path] = []
                modules[full_path].append(item.start_byte)
        
        duplicates = []
        for path, positions in modules.items():
            if len(positions) > 1:
                duplicates.append(f"  - module '{path}' declared at bytes {' and '.join(map(str, positions))}")
        
        if duplicates:
            return "Illegal duplicate declarations detected:\n" + "\n".join(duplicates) + \
                   "\nElixir requires module names to be unique.\n" + \
                   "The modification has been rejected to prevent invalid code."
        
        return None
    
    # =========================================================================
    # CONTENT HANDLING
    # =========================================================================
    
    def content_starts_with_attr_or_doc(self, code: str) -> bool:
        """Check if code starts with module attribute or doc comment."""
        code = code.lstrip()
        if code.startswith('@'):
            return True
        if code.startswith('#'):
            return True
        return False
    
    def format_candidates_diagnostic(self, content: str, target_path: str) -> str:
        """Format diagnostic message for failed target lookup."""
        items = self._build_item_index(content)
        target = self.parse_target_path(target_path)
        
        candidates = []
        for item in items:
            if item.kind in ELIXIR_MODULE_KEYWORDS:
                full_path = '.'.join(item.module_path + ([item.name] if item.name else []))
                candidates.append(f"  {item.kind} {full_path}")
            elif item.kind in ('def', 'defp', 'defmacro', 'defmacrop'):
                mod_path = '.'.join(item.module_path) + '.' if item.module_path else ''
                arity_str = f"/{item.arity}" if item.arity is not None else ""
                candidates.append(f"  {item.kind} {mod_path}{item.name}{arity_str}")
        
        if not candidates:
            return f"No declarations found in file. Target was: {target_path}"
        
        return f"Target '{target_path}' not found. Available declarations:\n" + "\n".join(candidates[:20])


# Register the handler
_handler = ElixirHandler()
register_handler('.ex', _handler)
register_handler('.exs', _handler)
