"""The fixes package can reach no network, file, process or dynamic-import facility. An ALLOWLIST of imports plus a
ban on the names that open those doors by another route. This is a tripwire for review, not a sandbox: the proof
that nothing writes to a site is also that fix generation takes no fetcher and runs no request (see test_api_fixes)."""
import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "app" / "fixes"
ALLOWED_MODULES = {"re", "json", "ast", "hashlib", "difflib", "dataclasses", "enum", "typing", "html", "urllib.parse",
                   "bs4", "unicodedata"}                  # unicodedata: removes invisible characters; no I/O
BANNED_NAMES = {"getattr", "setattr", "delattr", "globals", "locals", "vars", "__builtins__", "__import__", "eval",
                "exec", "compile", "open", "input", "breakpoint", "exit", "quit"}
BANNED_ATTRS = {"open", "write", "write_text", "write_bytes", "writelines", "system", "popen", "unlink", "mkdir",
                "touch", "rmdir", "remove", "rename", "makedirs", "urlopen", "request", "connect", "send", "sendall"}


def violations(source: str) -> list:
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [f"imports {a.name}" for a in node.names if a.name not in ALLOWED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:                                   # relative imports stay inside the package
                module = node.module or ""
                for a in node.names:
                    if module not in ALLOWED_MODULES and f"{module}.{a.name}" not in ALLOWED_MODULES:
                        problems.append(f"imports {module}.{a.name}")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f"uses the name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__") and node.attr != "__init__":
                problems.append(f"uses the dunder attribute {node.attr}")
            elif node.attr in BANNED_ATTRS:
                problems.append(f"uses the attribute {node.attr}")
            elif node.attr == "compile" and not (isinstance(node.value, ast.Name) and node.value.id == "re"):
                problems.append("uses compile on something other than re")
    return problems


def package_files():
    found = sorted(PKG.rglob("*.py"))                             # subdirectories too
    assert {f.name for f in found} >= {"queue.py", "propose.py", "generate.py", "validate.py", "baseline.py",
                                       "secrets.py", "diff.py", "models.py"}
    return found


@pytest.mark.parametrize("path", package_files(), ids=lambda p: str(p.relative_to(PKG)))
def test_the_fixes_package_uses_only_allowed_imports_and_no_door_to_files_processes_or_the_network(path):
    assert violations(path.read_text()) == []


@pytest.mark.parametrize("source", [
    "import os", "from os import path", "import importlib", "import urllib\nurllib.request.urlopen('x')",
    "from urllib import request", "import urllib.request", "import httpx", "import socket", "import subprocess",
    "import ssl", "import ctypes", "import tempfile", "import sqlite3", "import logging", "import http.server",
    "import xmlrpc.client", "import zipfile", "import webbrowser", "import pty", "import pathlib",
    "f = open\nf('x', 'w')", "open('x', 'w')", "getattr(__builtins__, 'op' + 'en')('x', 'w')",
    "__builtins__['open']('x', 'w')", "import sys\nsys.modules['importlib'].import_module('os')",
    "x = ().__class__.__bases__[0].__subclasses__()", "exec('1')", "eval('1')", "compile('1', 'x', 'exec')",
    "__import__('os')", "p.write_text('x')", "fh.write('x')", "os.system('id')", "globals()['x'] = 1", "vars()",
    "setattr(a, 'b', 1)", "x.compile('1')",
])
def test_the_checker_catches_every_bypass_the_reviewer_listed(source):
    assert violations(source) != [], source


@pytest.mark.parametrize("source", [
    "import json", "import re\nre.compile('x')", "from urllib.parse import quote", "from urllib import parse",
    "from bs4 import BeautifulSoup", "from .secrets import find_secrets", "from ..models import Report",
    "import unicodedata", "from dataclasses import dataclass, field", "x = 'a'.replace('a', 'b')",
])
def test_the_checker_allows_what_the_package_really_uses(source):
    assert violations(source) == []
