# src/vibemod/handlers/base.py
"""Base class for language handlers with full modification support."""

import os
import re
import textwrap
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List, Dict, Any

_HANDLERS: Dict[str, 'LanguageHandler'] = {}

class LanguageHandler(ABC):
    """Abstract base class for language-specific code modification handlers.

    Subclasses must implement:
    - _parse(content) -> root node
    - _byte_to_char(content, byte_offset) -> char offset
    - _get_children(node) -> list of child nodes
    - _get_declaration_name(node, content) -> name or None
    - find_declaration(content, target_path) -> (start, end) or None
    - find_all_declarations(content, target_path) -> list of (start, end)
    - find_header_end(content) -> char offset
    - validate_single_declaration(content) -> error message or None
    - validate_syntax(content, original_content=None) -> error message or None
    - get_decl_types() -> frozenset of declaration node types

    Optional overrides:
    - parse_target_path(target_path) -> parsed target info
    - unwrap_content_if_needed(content, target) -> possibly unwrapped content
    - get_insertion_point(content, target_path) -> char offset or None
    """

    @abstractmethod
    def _parse(self, content: str) -> Any:
        """Parse source and return the root node."""
        pass

    @abstractmethod
    def _byte_to_char(self, content: str, byte_offset: int) -> int:
        """Convert UTF-8 byte offset to character offset."""
        pass

    @abstractmethod
    def _get_children(self, node: Any) -> List[Any]:
        """Get children of a node as a list."""
        pass

    @abstractmethod
    def _get_declaration_name(self, node: Any, content: str) -> Optional[str]:
        """Extract the name from a declaration node."""
        pass

    @abstractmethod
    def find_declaration(self, content: str, target_path: str) -> Optional[Tuple[int, int]]:
        """Find the span of a declaration by target path."""
        pass

    @abstractmethod
    def find_all_declarations(self, content: str, target_path: str) -> List[Tuple[int, int]]:
        """Find all declarations matching the target path."""
        pass

    @abstractmethod
    def find_header_end(self, content: str) -> int:
        """Find where the header ends (first major declaration)."""
        pass

    @abstractmethod
    def validate_single_declaration(self, content: str) -> Optional[str]:
        """Validate content contains exactly one declaration."""
        pass

    @abstractmethod
    def validate_syntax(self, content: str, original_content: str=None) -> Optional[str]:
        """Validate syntax, optionally comparing to original for pre-existing errors."""
        pass

    @abstractmethod
    def get_decl_types(self) -> frozenset:
        """Return the set of declaration node types for this language."""
        pass

    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
        """Extract declaration name from a code region."""
        snippet = content[start:end]
        root = self._parse(snippet)
        for child in self._get_children(root):
            if child.type in self.get_decl_types():
                return self._get_declaration_name(child, snippet)
        return None

    def parse_target_path(self, target_path: str) -> Any:
        """Parse a target path into structured form. Override for complex syntax."""
        return target_path

    def is_insertion_target(self, target_path: str) -> bool:
        """Check if target path is an insertion anchor. Override if needed."""
        return False

    def get_insertion_point(self, content: str, target_path: str) -> Optional[int]:
        """Get insertion point for new content. Override if needed."""
        return None

    def unwrap_content_if_needed(self, content: str, target_path: str) -> str:
        """Unwrap content if user wrapped it unnecessarily. Override if needed."""
        return content

    def content_starts_with_attr_or_doc(self, code: str) -> bool:
        """Check if code starts with attributes or doc comments. Override per language."""
        return False

    def find_decl_start_in_span(self, span_text: str) -> int:
        """Find where actual declaration starts in span (after attrs/docs)."""
        root = self._parse(span_text)
        for child in self._get_children(root):
            if child.type in self.get_decl_types():
                return self._byte_to_char(span_text, child.start_byte)
        return 0

    def adjust_span_for_attributes(self, source: str, span_start: int, span_end: int, new_content: str) -> Tuple[int, int]:
        """Adjust span if original has attrs/docs but replacement doesn't."""
        if self.content_starts_with_attr_or_doc(new_content):
            return (span_start, span_end)
        original_span_text = source[span_start:span_end]
        if not self.content_starts_with_attr_or_doc(original_span_text):
            return (span_start, span_end)
        decl_offset = self.find_decl_start_in_span(original_span_text)
        if decl_offset > 0:
            return (span_start + decl_offset, span_end)
        return (span_start, span_end)

    def validate_no_illegal_duplicates(self, content: str) -> Optional[str]:
        """Check for illegal duplicate declarations. Override if needed."""
        return None

    def format_candidates_diagnostic(self, content: str, target_path: str) -> str:
        """Format diagnostic message for failed target lookup. Override for richer output."""
        return f'No match found for target path: {target_path}'

    def modify_declaration(self, file_path: str, source: str, target_path: str, content: Optional[str], remove: bool, debug_dump_func=None) -> str:
        """
        Modify a declaration in source code.

        Args:
            file_path: Path to file (for error messages)
            source: Current file content
            target_path: Target declaration path
            content: New content (None if removing)
            remove: True to remove, False to add/replace
            debug_dump_func: Optional function to dump debug info on error

        Returns:
            Modified source code

        Raises:
            ValueError: If modification would create invalid code
        """
        source_before = source

        def validate_and_return(new_source: str) -> str:
            """Validate the new source and return it or raise."""
            syntax_error = self.validate_syntax(new_source, original_content=source_before)
            if syntax_error:
                if debug_dump_func:
                    debug_dir = debug_dump_func(file_path=file_path, target_path=target_path, content=content, source_before=source_before, source_after=new_source, error_message=syntax_error, remove=remove)
                    raise ValueError(f'Modification would create syntactically invalid code:\n{syntax_error}\n\nDebug files written to: {debug_dir}')
                raise ValueError(f'Modification would create syntactically invalid code:\n{syntax_error}')
            dup_error = self.validate_no_illegal_duplicates(new_source)
            if dup_error:
                raise ValueError(f'Modification would create invalid code:\n{dup_error}')
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
        content = self.unwrap_content_if_needed(content, target_path)
        single_decl_error = self.validate_single_declaration(content)
        if single_decl_error:
            if debug_dump_func:
                debug_dir = debug_dump_func(file_path=file_path, target_path=target_path, content=content, source_before=source_before, source_after=content, error_message=single_decl_error, remove=remove)
                raise ValueError(f'Invalid declare content:\n{single_decl_error}\n\nDebug files written to: {debug_dir}')
            raise ValueError(f'Invalid declare content:\n{single_decl_error}')
        if self.is_insertion_target(target_path):
            insertion_point = self.get_insertion_point(source, target_path)
            if insertion_point is None:
                diagnostic = self.format_candidates_diagnostic(source, target_path)
                raise ValueError(f'Cannot determine insertion point.\n{diagnostic}')
            before = source[:insertion_point].rstrip()
            after = source[insertion_point:].lstrip()
            new_source = before + '\n\n' + content + '\n\n' + after
            new_source = re.sub('\\n{3,}', '\n\n', new_source)
            return validate_and_return(new_source)
        spans = self.find_all_declarations(source, target_path)
        if spans:
            adjusted_spans = [self.adjust_span_for_attributes(source, s, e, content) for s, e in spans]
            adjusted_spans.sort(key=lambda s: s[0], reverse=True)
            new_source = source
            for start, end in adjusted_spans:
                new_source = new_source[:start] + content + new_source[end:]
            return validate_and_return(new_source)
        new_source = source.rstrip() + '\n\n' + content + '\n' if source.strip() else content + '\n'
        return validate_and_return(new_source)
_HANDLERS: Dict[str, LanguageHandler] = {}

def register_handler(extension: str, handler: 'LanguageHandler') -> None:
    """Register a handler for a file extension."""
    _HANDLERS[extension] = handler

def get_handler(file_path: str) -> 'LanguageHandler':
    """Get the appropriate handler for a file based on its extension."""
    ext = os.path.splitext(file_path)[1]
    if ext not in _HANDLERS:
        supported = ', '.join(sorted(_HANDLERS.keys()))
        raise ValueError(f"Unsupported file extension '{ext}' for file '{file_path}'. Supported extensions: {supported}")
    return _HANDLERS[ext]