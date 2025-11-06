import os
import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def git_repo_tmp(tmp_path, monkeypatch):
    """
    Create a temporary git repo because apply_modspec(...) runs git.
    """
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True)
    # configure minimal identity to allow commits
    subprocess.run(["git", "config", "user.email", "you@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Your Name"], cwd=repo, check=True)
    return repo


@pytest.fixture
def write_file(tmp_path):
    def _write(relpath: str, content: str):
        p = tmp_path / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p
    return _write
