"""Runs the Node unit tests for the web UI. Node 20 or newer is needed only for these tests; the UI itself has no
build step. If Node is not installed the test is skipped with a reason (it is never silently green)."""
import glob
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_node_ui_tests_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the UI unit tests were not run")
    files = sorted(glob.glob(str(BACKEND / "tests" / "ui" / "*.test.mjs")))
    assert files, "there must be UI unit tests"
    done = subprocess.run([node, "--test", *files], cwd=BACKEND, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
