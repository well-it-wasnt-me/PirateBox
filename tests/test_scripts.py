"""Script hygiene tests: because exit code 126 is a terrible way to learn about chmod."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_are_executable():
    """CI and systemd run these directly; a missing exec bit only shows up mid-release."""
    scripts = sorted((ROOT / "scripts").rglob("*.sh"))
    assert scripts, "no scripts found, which is its own kind of problem"
    not_executable = [str(path.relative_to(ROOT)) for path in scripts if not os.access(path, os.X_OK)]
    assert not_executable == []
