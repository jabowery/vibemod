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
    signature: Optional[str] = None  # Normalized first argument pattern for functions
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
    occurrence: Optional[int] = None  # @N occurrence selector (integer)
    signature_pattern: Optional[str] = None  # @pattern signature matcher
    
    @property
    def is_module_target(self) -> bool:
        return bool(self.module_path) and self.item_name is None
    
    @property
    def is_function_target(self) -> bool:
        return self.item_name is not None
    
    @property
    def has_signature_match(self) -> bool:
        return self.signature_pattern is not None


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
    
    def _extract_function_signature(self, node: Any, content: str) -> Optional[str]:
        """
        Extract a normalized signature from a function definition.
        
        The signature is the first argument's pattern, normalized:
        - Variable names replaced with _
        - Whitespace normalized
        
        Examples:
            def handle_info({:ts_event, :out, tuple}, s) -> "{:ts_event, :out, _}"
            def handle_info(:timeout, s) -> ":timeout"
            def process(%User{name: name}, opts) -> "%User{name: _}"
        """
        args = None
        for child in self._get_children(node):
            if child.type == 'arguments':
                args = child
                break
        
        if args is None:
            return None
        
        # Find the function call within arguments
        for child in self._get_children(args):
            if child.type == 'call':
                # Get the function's arguments
                call_args = None
                for subchild in self._get_children(child):
                    if subchild.type == 'arguments':
                        call_args = subchild
                        break
                
                if call_args is None:
                    return None
                
                # Get the first argument
                first_arg = None
                for subchild in self._get_children(call_args):
                    if subchild.type not in ('(', ')', ','):
                        first_arg = subchild
                        break
                
                if first_arg is None:
                    return None
                
                # Extract and normalize the first argument
                start = self._byte_to_char(content, first_arg.start_byte)
                end = self._byte_to_char(content, first_arg.end_byte)
                raw_sig = content[start:end].strip()
                
                return self._normalize_signature(raw_sig)
            
            elif child.type == 'identifier':
                # def foo - no args, no signature
                return None
            
            elif child.type == 'binary_operator':
                # def foo(x) when is_integer(x) - has a when clause
                # The left side is the actual function call
                left = child.child_by_field_name('left')
                if left and left.type == 'call':
                    call_args = None
                    for subchild in self._get_children(left):
                        if subchild.type == 'arguments':
                            call_args = subchild
                            break
                    
                    if call_args:
                        first_arg = None
                        for subchild in self._get_children(call_args):
                            if subchild.type not in ('(', ')', ','):
                                first_arg = subchild
                                break
                        
                        if first_arg:
                            start = self._byte_to_char(content, first_arg.start_byte)
                            end = self._byte_to_char(content, first_arg.end_byte)
                            raw_sig = content[start:end].strip()
                            return self._normalize_signature(raw_sig)
        
        return None
    
    def _normalize_signature(self, sig: str) -> str:
        """
        Normalize a signature pattern for matching.
        
        - Replace variable names (lowercase identifiers) with _
        - Normalize whitespace
        - Keep atoms, tuples, structs, literals intact
        """
        # Tokenize and normalize
        result = []
        i = 0
        
        while i < len(sig):
            c = sig[i]
            
            # Skip whitespace, normalize to single space
            if c.isspace():
                if result and result[-1] != ' ':
                    result.append(' ')
                i += 1
                continue
            
            # Atoms - keep as is
            if c == ':':
                result.append(c)
                i += 1
                # Read the atom name (can include ? or ! at the end)
                while i < len(sig) and (sig[i].isalnum() or sig[i] == '_'):
                    result.append(sig[i])
                    i += 1
                # Elixir atoms can end with ? or !
                if i < len(sig) and sig[i] in '?!':
                    result.append(sig[i])
                    i += 1
                continue
            
            # Strings - keep as is
            if c == '"':
                result.append(c)
                i += 1
                while i < len(sig) and sig[i] != '"':
                    if sig[i] == '\\' and i + 1 < len(sig):
                        result.append(sig[i])
                        result.append(sig[i + 1])
                        i += 2
                    else:
                        result.append(sig[i])
                        i += 1
                if i < len(sig):
                    result.append(sig[i])  # closing quote
                    i += 1
                continue
            
            # Struct names (uppercase start) - keep as is
            if c == '%':
                result.append(c)
                i += 1
                # Read the struct name
                while i < len(sig) and (sig[i].isalnum() or sig[i] in '_.'):
                    result.append(sig[i])
                    i += 1
                continue
            
            # Numbers - keep as is
            if c.isdigit():
                while i < len(sig) and (sig[i].isdigit() or sig[i] == '.'):
                    result.append(sig[i])
                    i += 1
                continue
            
            # Punctuation and operators - keep as is
            if c in '{}[](),|=><+-*/_^&!?@#':
                result.append(c)
                i += 1
                continue
            
            # Identifiers
            if c.isalpha() or c == '_':
                # Read the full identifier
                ident_start = i
                while i < len(sig) and (sig[i].isalnum() or sig[i] == '_'):
                    i += 1
                ident = sig[ident_start:i]
                
                # Check if it's a variable (starts with lowercase or _) vs module/atom
                if ident[0].islower() or ident[0] == '_':
                    # It's a variable - replace with _
                    result.append('_')
                else:
                    # It's a module name or similar - keep as is
                    result.append(ident)
                continue
            
            # Anything else - keep as is
            result.append(c)
            i += 1
        
        normalized = ''.join(result).strip()
        # Collapse multiple spaces
        normalized = re.sub(r' +', ' ', normalized)
        # Remove spaces around punctuation for cleaner matching
        normalized = re.sub(r'\s*([{}[\](),|])\s*', r'\1', normalized)
        
        return normalized
    
    def _signatures_match(self, indexed_sig: Optional[str], target_sig: str) -> bool:
        """
        Check if an indexed signature matches a target signature pattern.
        
        The target pattern can use _ as a wildcard.
        """
        if indexed_sig is None:
            return False
        
        # Normalize the target signature the same way
        target_normalized = self._normalize_signature(target_sig)
        
        # Direct match
        if indexed_sig == target_normalized:
            return True
        
        # Pattern match with _ as wildcard
        # Convert both to a simple pattern matching
        return self._pattern_match(indexed_sig, target_normalized)
    
    def _pattern_match(self, actual: str, pattern: str) -> bool:
        """
        Match actual signature against pattern.
        Both are already normalized.
        
        _ in pattern matches any single "token" in actual.
        """
        # Simple approach: split by structure and compare
        # For now, just check if the structural parts match
        
        # If pattern has wildcards, try to match structurally
        if '_' not in pattern:
            return actual == pattern
        
        # Extract the "skeleton" - the structural parts without variables
        def extract_skeleton(s):
            # Keep only: atoms, punctuation, module names
            # This gives us the pattern structure
            parts = []
            i = 0
            while i < len(s):
                c = s[i]
                if c == ':':
                    # Atom - keep it (including ? or ! suffix)
                    atom = ':'
                    i += 1
                    while i < len(s) and (s[i].isalnum() or s[i] == '_'):
                        atom += s[i]
                        i += 1
                    # Elixir atoms can end with ? or !
                    if i < len(s) and s[i] in '?!':
                        atom += s[i]
                        i += 1
                    parts.append(atom)
                elif c in '{}[](),|%':
                    parts.append(c)
                    i += 1
                elif c == '_':
                    parts.append('_')
                    i += 1
                elif c.isupper():
                    # Module name
                    name = ''
                    while i < len(s) and (s[i].isalnum() or s[i] in '_.'):
                        name += s[i]
                        i += 1
                    parts.append(name)
                else:
                    i += 1
            return ''.join(parts)
        
        actual_skeleton = extract_skeleton(actual)
        pattern_skeleton = extract_skeleton(pattern)
        
        return actual_skeleton == pattern_skeleton
    
    # =========================================================================
    # TARGET PATH PARSING
    # =========================================================================
    
    def parse_target_path(self, target_path: str) -> TargetPath:
        """
        Parse an Elixir target path.
        
        Supported formats:
        - MyModule                         -> module
        - MyModule.SubModule               -> nested module
        - MyModule.function_name           -> function in module
        - MyModule.function_name/2         -> function with arity
        - function_name                    -> top-level function (rare)
        - defp:function_name               -> private function
        - MyModule.defp:func               -> private function in module
        - target@N                         -> Nth occurrence (integer)
        - target/2@{:pattern, _}           -> match by first arg pattern
        - target/2@:atom                   -> match by first arg atom
        """
        result = TargetPath()
        path = target_path.strip()
        
        # Check for signature pattern @pattern (non-numeric after @)
        # Must check this BEFORE occurrence to distinguish @1 from @{:tuple}
        sig_match = re.search(r'@([^@].*)$', path)
        if sig_match:
            potential_sig = sig_match.group(1)
            # Check if it's a pure integer (occurrence) or a pattern (signature)
            if re.match(r'^\d+$', potential_sig):
                # It's an occurrence selector @N
                result.occurrence = int(potential_sig)
                path = path[:sig_match.start()]
            else:
                # It's a signature pattern
                result.signature_pattern = potential_sig.strip()
                path = path[:sig_match.start()]
        
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
                    signature = None
                    if keyword in ('def', 'defp', 'defmacro', 'defmacrop', 'defguard', 'defguardp'):
                        arity = self._extract_function_arity(child)
                        signature = self._extract_function_signature(child, content)
                    
                    item = IndexedItem(
                        kind=keyword,
                        name=name,
                        module_path=list(module_path),
                        start_byte=start_char,
                        end_byte=end_char,
                        arity=arity,
                        signature=signature,
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
            
            # Check signature pattern if specified
            if target.signature_pattern is not None:
                if not self._signatures_match(item.signature, target.signature_pattern):
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
                sig_str = f"  sig: {item.signature}" if item.signature else ""
                candidates.append(f"  {item.kind} {mod_path}{item.name}{arity_str}{sig_str}")
        
        if not candidates:
            return f"No declarations found in file. Target was: {target_path}"
        
        return f"Target '{target_path}' not found. Available declarations:\n" + "\n".join(candidates[:20])
    
    def _extract_signature_from_content(self, content: str) -> Optional[str]:
        """
        Extract the function signature from replacement content.
        
        If content contains a function definition (def/defp/etc), extract
        the normalized first argument pattern to use for disambiguation.
        
        Returns:
            Normalized signature string, or None if not extractable
        """
        content = content.strip()
        
        # Skip leading attributes like @impl, @doc
        root = self._parse(content)
        
        for child in self._get_children(root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword in ('def', 'defp', 'defmacro', 'defmacrop', 'defguard', 'defguardp'):
                    # Found a function definition - extract its signature
                    return self._extract_function_signature(child, content)
            elif child.type == 'unary_operator':
                # Skip attributes (@impl, @doc, etc.)
                continue
        
        return None
    
    def _get_content_declaration_kind(self, content: str) -> Optional[str]:
        """
        Get the declaration kind (def, defstruct, defmodule, etc.) from content.
        
        Returns the keyword of the first declaration found, or None.
        """
        content = content.strip()
        root = self._parse(content)
        
        for child in self._get_children(root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword in ELIXIR_DEF_KEYWORDS:
                    return keyword
            elif child.type == 'unary_operator':
                # Skip attributes
                continue
        
        return None
    
    def _infer_target_from_content(self, module_target: str, content: str, content_kind: str) -> Optional[str]:
        """
        Infer the correct target path when a module is targeted but content is not a module.
        
        For example:
        - Target: "MyModule.S", Content: "defstruct ..." -> "MyModule.S.defstruct"
        - Target: "MyModule.S", Content: "def foo ..." -> "MyModule.S.foo"
        """
        content = content.strip()
        root = self._parse(content)
        
        for child in self._get_children(root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword == content_kind:
                    if keyword == 'defstruct':
                        # defstruct has no name, use the keyword itself
                        return f"{module_target}.defstruct"
                    else:
                        # For def/defp etc., extract the function name
                        name = self._extract_def_name(child, keyword)
                        if name:
                            prefix = 'defp:' if keyword in ('defp', 'defmacrop', 'defguardp') else ''
                            return f"{module_target}.{prefix}{name}"
            elif child.type == 'unary_operator':
                continue
        
        return None
    
    def modify_declaration(self, file_path: str, source: str, target_path: str, 
                          content: Optional[str], remove: bool, debug_dump_func=None) -> str:
        """
        Modify a declaration in Elixir source code.
        
        Overrides base to handle Elixir-specific concerns:
        - Infers signature from content to disambiguate multi-clause functions
        - Infers target from content when module target but non-module content
        - Preserves indentation when replacing function clauses
        """
        target = self.parse_target_path(target_path)
        
        # Check for module target with non-module content
        # e.g., target is "MyModule.S" (a module) but content is "defstruct ..."
        if target.is_module_target and not remove and content:
            content_kind = self._get_content_declaration_kind(content)
            if content_kind and content_kind not in ELIXIR_MODULE_KEYWORDS:
                # Content is not a module (it's a def, defstruct, etc.)
                # Infer the actual target by appending the content's kind/name
                inferred_target = self._infer_target_from_content(target_path, content, content_kind)
                if inferred_target:
                    target_path = inferred_target
                    target = self.parse_target_path(target_path)
        
        # Check for ambiguous multi-clause function matches
        if target.is_function_target and not remove and content:
            spans = self.find_all_declarations(source, target_path)
            if len(spans) > 1 and target.signature_pattern is None and target.occurrence is None:
                # Multiple matches and no disambiguation provided
                # Try to infer signature from the content
                content_signature = self._extract_signature_from_content(content)
                
                if content_signature:
                    # Use the inferred signature to disambiguate
                    new_target_path = f"{target_path}@{content_signature}"
                    target = self.parse_target_path(new_target_path)
                    target_path = new_target_path
                    
                    # Re-check if we now have a single match
                    spans = self.find_all_declarations(source, target_path)
                
                if len(spans) > 1:
                    # Still ambiguous - require explicit disambiguation
                    items = self._build_item_index(source)
                    matches = self._find_matches(items, self.parse_target_path(target_path.split('@')[0]))
                    
                    # Build helpful error message
                    clause_info = []
                    for i, item in enumerate(matches, 1):
                        if item.signature:
                            clause_info.append(f"  @{i} or @{item.signature}")
                        else:
                            clause_info.append(f"  @{i}")
                    
                    raise ValueError(
                        f"Ambiguous target: '{target_path}' matches {len(matches)} function clauses.\n"
                        f"Please disambiguate using occurrence (@N) or signature pattern (@pattern):\n"
                        + "\n".join(clause_info) + "\n\n"
                        f"Example: {target_path}@1 or {target_path}@{matches[0].signature if matches[0].signature else '{{:pattern, _}}'}"
                    )
        
        # For single matches or removals, handle with indentation preservation
        if not remove and content:
            spans = self.find_all_declarations(source, target_path)
            if spans:
                # Preserve indentation from original
                start, end = spans[0]
                original_text = source[start:end]
                
                # Find the indentation of the original declaration
                line_start = source.rfind('\n', 0, start) + 1
                original_indent = ''
                for c in source[line_start:start]:
                    if c in ' \t':
                        original_indent += c
                    else:
                        break
                
                # Check if content needs indentation adjustment
                content_stripped = content.strip()
                content_lines = content_stripped.split('\n')
                
                # Re-indent content to match original
                if original_indent and content_lines:
                    # Find the base indentation of the content
                    content_base_indent = ''
                    for c in content_lines[0]:
                        if c in ' \t':
                            content_base_indent += c
                        else:
                            break
                    
                    # Re-indent all lines
                    reindented_lines = []
                    for line in content_lines:
                        if line.strip():
                            # Remove content's base indent and add original indent
                            if line.startswith(content_base_indent):
                                line = original_indent + line[len(content_base_indent):]
                            else:
                                line = original_indent + line.lstrip()
                        reindented_lines.append(line)
                    
                    content = '\n'.join(reindented_lines)
                
                # Do the replacement directly (don't call super which would dedent)
                new_source = source[:start] + content + source[end:]
                
                # Validate syntax
                syntax_error = self.validate_syntax(new_source, original_content=source)
                if syntax_error:
                    if debug_dump_func:
                        debug_dir = debug_dump_func(
                            file_path=file_path, target_path=target_path, 
                            content=content, source_before=source, 
                            source_after=new_source, error_message=syntax_error, remove=remove
                        )
                        raise ValueError(f'Modification would create syntactically invalid code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
                    raise ValueError(f'Modification would create syntactically invalid code:\n{syntax_error}')
                
                return new_source
        
        # For removals or new declarations, delegate to base class
        return super().modify_declaration(file_path, source, target_path, content, remove, debug_dump_func)
    
    # =========================================================================
    # UPDATE HEADER
    # =========================================================================
    
    def update_header(self, source: str, new_header: str) -> str:
        """
        Replace the header section of an Elixir source file.
        
        Special Elixir behavior:
        - If new_header starts with 'defmodule', treat it as module-internal header:
          Replace content inside that module up to (but not including) the first def*.
        - Otherwise, replace file-level content before the first defmodule/def*.
        
        Args:
            source: Current file content
            new_header: New header content
            
        Returns:
            Modified source code
        """
        new_header = new_header.strip()
        
        # Check if new_header starts with defmodule
        if self._content_starts_with_defmodule(new_header):
            return self._update_module_header(source, new_header)
        else:
            # Standard file-level header update
            header_end = self.find_header_end(source)
            return new_header + '\n\n' + source[header_end:]
    
    def _content_starts_with_defmodule(self, content: str) -> bool:
        """Check if content starts with a defmodule declaration."""
        stripped = content.lstrip()
        return stripped.startswith('defmodule ')
    
    def _update_module_header(self, source: str, new_header: str) -> str:
        """
        Update the header section inside a module.
        
        Replaces everything from 'defmodule ModName do' up to (but not including)
        the first def/defp/defmacro/etc inside that module.
        """
        # Parse new_header to get the module name
        new_header_root = self._parse(new_header)
        new_module_name = None
        
        for child in self._get_children(new_header_root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword == 'defmodule':
                    new_module_name = self._extract_def_name(child, keyword)
                    break
        
        if not new_module_name:
            # Couldn't parse module name, fall back to file-level update
            header_end = self.find_header_end(source)
            return new_header + '\n\n' + source[header_end:]
        
        # Find the matching module in source
        source_root = self._parse(source)
        module_node = None
        
        for child in self._get_children(source_root):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword == 'defmodule':
                    name = self._extract_def_name(child, keyword)
                    if name == new_module_name:
                        module_node = child
                        break
        
        if not module_node:
            # Module not found in source, append the new header content
            return source.rstrip() + '\n\n' + new_header + '\n'
        
        # Find the do_block inside the module
        do_block = self._find_do_block(module_node)
        if not do_block:
            # No do block, replace entire module
            start = self._byte_to_char(source, module_node.start_byte)
            end = self._byte_to_char(source, module_node.end_byte)
            return source[:start] + new_header + source[end:]
        
        # Find the first def* inside the do_block
        first_def_start = None
        for child in self._get_children(do_block):
            if child.type == 'call':
                keyword = self._get_call_keyword(child)
                if keyword in ('def', 'defp', 'defmacro', 'defmacrop', 'defguard', 'defguardp', 'defdelegate'):
                    first_def_start = self._byte_to_char(source, child.start_byte)
                    break
        
        if first_def_start is None:
            # No def* found, replace entire module
            start = self._byte_to_char(source, module_node.start_byte)
            end = self._byte_to_char(source, module_node.end_byte)
            return source[:start] + new_header + source[end:]
        
        # Check if there are attributes (like @doc) right before the first def
        # We want to preserve those with the def, not replace them
        first_def_start = self._find_attr_start_before_def(source, do_block, first_def_start)
        
        # Get module start position
        module_start = self._byte_to_char(source, module_node.start_byte)
        
        # Get the end of the module (the 'end' keyword)
        module_end = self._byte_to_char(source, module_node.end_byte)
        
        # Build the new source:
        # - Everything before this module
        # - New header content (which includes defmodule ... do and module-level stuff)
        # - Everything from first def* to module end
        # - Everything after module end
        
        # The new_header should end with the content up to first def
        # We need to ensure proper newlines
        rest_of_module = source[first_def_start:module_end]
        
        # Ensure new_header ends properly for concatenation
        if not new_header.endswith('\n'):
            new_header = new_header + '\n'
        
        # Add proper spacing before the first def
        new_header = new_header.rstrip() + '\n\n  '
        
        return source[:module_start] + new_header + rest_of_module.lstrip() + source[module_end:]
    
    def _find_attr_start_before_def(self, source: str, do_block: Any, def_start: int) -> int:
        """
        Find if there are @doc or similar attributes right before the def.
        If so, return the start of those attributes to preserve them with the def.
        """
        # Look for unary_operator (@) nodes right before the def
        prev_attr_start = def_start
        
        for child in self._get_children(do_block):
            if child.type == 'unary_operator':
                child_end = self._byte_to_char(source, child.end_byte)
                child_start = self._byte_to_char(source, child.start_byte)
                
                # Check if this attribute is right before our def (only whitespace between)
                between = source[child_end:def_start].strip()
                if between == '':
                    # Check if it's a function-level attribute (not @moduledoc etc)
                    attr_text = source[child_start:child_end].strip()
                    attr_name = attr_text.split()[0] if attr_text else ''
                    if attr_name not in ('@moduledoc', '@behaviour', '@callback', '@type', '@typep', '@opaque', '@spec'):
                        prev_attr_start = min(prev_attr_start, child_start)
                        def_start = child_start  # Look for more attrs before this one
        
        return prev_attr_start


# Register the handler
_handler = ElixirHandler()
register_handler('.ex', _handler)
register_handler('.exs', _handler)
