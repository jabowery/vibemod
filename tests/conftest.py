# tests/conftest.py
"""Pytest configuration and shared fixtures."""

import pytest
import sys
from pathlib import Path

# Add the src directory to the path
src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))
