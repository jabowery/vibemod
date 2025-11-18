# src/vibemod/cli.py

import argparse
import sys
from pathlib import Path

from .modify_code import apply_modspec


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Apply MMM modification spec to the current git repo."
    )
    parser.add_argument(
        "spec_file",
        type=Path,
        help="Path to the MMM spec file.",
    )
    args = parser.parse_args(argv)

    apply_modspec(str(args.spec_file))
