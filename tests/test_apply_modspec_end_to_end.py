import subprocess
from pathlib import Path
import textwrap

from modify_code import apply_modspec


def test_apply_modspec_creates_file_and_commits(git_repo_tmp):
    repo = git_repo_tmp
    spec = repo / "spec.md"
    spec.write_text(
        textwrap.dedent(
            """
            MMM modification_description MMM
            add util file

            MMM create_file MMM
            util.py
            @@@@@@
            def util():
                return 42
            """
        ).lstrip(),
        encoding="utf-8",
    )

    # need at least one commit in the repo for rev-parse HEAD to work
    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    apply_modspec(str(spec))

    created = repo / "util.py"
    assert created.exists()
    assert "def util" in created.read_text(encoding="utf-8")

    # ensure a commit got created with our description
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert "add util file" in log


def test_apply_modspec_declare_into_existing_file(git_repo_tmp):
    repo = git_repo_tmp
    # base commit
    (repo / "mod.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "mod.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)

    spec = repo / "spec.md"
    spec.write_text(
        textwrap.dedent(
            """
            MMM modification_description MMM
            add declared func

            MMM declare MMM
            mod.py.newfunc
            @@@@@@
            def newfunc():
                return 99
            """
        ).lstrip(),
        encoding="utf-8",
    )

    apply_modspec(str(spec))

    content = (repo / "mod.py").read_text(encoding="utf-8")
    assert "def base" in content
    assert "def newfunc" in content
