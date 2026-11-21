import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "app" / "fixes"
BANNED_MODULES = {"httpx", "requests", "urllib.request", "urllib3", "http.client", "socket", "ftplib", "smtplib",
                  "subprocess", "shutil", "os", "pathlib", "aiohttp", "websockets", "asyncio", "multiprocessing",
                  "importlib", "builtins"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__"}          # builtins, called by bare name
BANNED_ATTRS = {"open", "write_text", "write_bytes", "write", "writelines", "system", "popen", "unlink", "mkdir",
                "touch", "rmdir"}


def files():
    found = sorted(PKG.glob("*.py"))
    assert {f.name for f in found} >= {"queue.py", "propose.py", "generate.py", "validate.py", "baseline.py"}
    return found


def imported_names(node):
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return [module] + [f"{module}.{a.name}" for a in node.names]
    return []


def banned_import(name):
    return name in BANNED_MODULES or name.split(".")[0] in BANNED_MODULES


def banned_call(node):
    fn = node.func
    return ((isinstance(fn, ast.Name) and fn.id in BANNED_NAMES)
            or (isinstance(fn, ast.Attribute) and fn.attr in BANNED_ATTRS))


def test_the_fixes_package_imports_no_network_file_or_process_library():
    for f in files():
        for node in ast.walk(ast.parse(f.read_text())):
            for name in imported_names(node):
                assert not banned_import(name), f"{f.name} imports {name}"


def test_the_fixes_package_never_opens_writes_or_runs_anything():
    for f in files():
        for node in ast.walk(ast.parse(f.read_text())):
            assert not (isinstance(node, ast.Call) and banned_call(node)), f"{f.name} line {node.lineno} makes a banned call"


def test_the_checker_itself_catches_what_it_is_meant_to_catch():
    for bad in ("import os", "from urllib import request", "import httpx", "from pathlib import Path", "import os.path"):
        names = [n for node in ast.walk(ast.parse(bad)) for n in imported_names(node)]
        assert any(banned_import(n) for n in names), bad
    for fine in ("import json", "from urllib.parse import quote", "import re", "from bs4 import BeautifulSoup"):
        names = [n for node in ast.walk(ast.parse(fine)) for n in imported_names(node)]
        assert not any(banned_import(n) for n in names), fine
    calls = [n for n in ast.walk(ast.parse("open('x', 'w'); p.write_text('y'); re.compile('z'); eval('1')"))
             if isinstance(n, ast.Call)]
    assert [banned_call(c) for c in calls] == [True, True, False, True]
