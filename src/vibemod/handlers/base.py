# src/vibemod/handlers/base.py
"""Base class and registry for language handlers."""

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Type


class LanguageHandler(ABC):
    """Abstract base class for language-specific code modification handlers."""

    @abstractmethod
    def find_declaration(
        self, content: str, target_path: str
    ) -> Optional[Tuple[int, int]]:
        """
        Find a declaration in source content.

        Args:
            content: The source file content as a string.
            target_path: Dot-notation path to the declaration (e.g., "ClassName.method").

        Returns:
            Tuple of (start_byte, end_byte) if found, None otherwise.
        """
        pass

    @abstractmethod
    def find_header_end(self, content: str) -> int:
        """
        Find the byte offset where the "header" ends and declarations begin.

        The header typically includes imports, module docstrings, and top-level
        constants/assignments that appear before any class or function definitions.

        Args:
            content: The source file content as a string.

        Returns:
            Byte offset of the first declaration, or len(content) if none found.
        """
        pass

    @abstractmethod
    def get_declaration_name(self, content: str, start: int, end: int) -> Optional[str]:
        """
        Extract the name of a declaration from a code region.

        Args:
            content: The source file content.
            start: Start byte offset.
            end: End byte offset.

        Returns:
            The declaration name, or None if not determinable.
        """
        pass


# Global handler registry
_HANDLERS: Dict[str, LanguageHandler] = {}


def register_handler(extension: str, handler: LanguageHandler) -> None:
    """Register a handler for a file extension."""
    _HANDLERS[extension] = handler


def get_handler(file_path: str) -> LanguageHandler:
    """
    Get the appropriate handler for a file based on its extension.

    Args:
        file_path: Path to the file.

    Returns:
        The appropriate LanguageHandler instance.

    Raises:
        ValueError: If no handler is registered for the file extension.
    """
    ext = os.path.splitext(file_path)[1]
    if ext not in _HANDLERS:
        supported = ", ".join(sorted(_HANDLERS.keys()))
        raise ValueError(
            f"Unsupported file extension '{ext}' for file '{file_path}'. "
            f"Supported extensions: {supported}"
        )
    return _HANDLERS[ext]

