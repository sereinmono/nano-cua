"""Static checks that the base stays free of concrete I/O and action semantics.

Requirements: the base package must contain only the generic ports/loop/policy/
mock modules - no browser, desktop, bash or model compatibility code, and no
dependency on playwright / httpx / a VLM SDK. Such layers live outside this
repository and are injected at composition time.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import nano_cua

PACKAGE_DIR = Path(nano_cua.__file__).parent
EXPECTED_MODULES = {"__init__.py", "contracts.py", "runner.py", "policy.py", "mock.py"}
CORE_MODULES = ("contracts.py", "runner.py", "policy.py", "mock.py")
FORBIDDEN_IMPORT_ROOTS = {"playwright", "httpx", "openai", "anthropic", "requests", "aiohttp", "selenium"}
FORBIDDEN_ADAPTER_MODULES = ("nano_cua.adapters", "nano_cua.cli")
FORBIDDEN_LITERALS = {"click", "type_text", "press_key", "screenshot", "keyboard", "mouse"}


def _parse(name: str) -> ast.Module:
    return ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    """Absolute module paths, including relative imports resolved to nano_cua.*."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                # Core modules live directly in the nano_cua package.
                if node.module:
                    modules.add(f"nano_cua.{node.module}")
                else:
                    modules.update(f"nano_cua.{alias.name}" for alias in node.names)
            elif node.level > 1:
                if node.module:
                    modules.add(node.module)
                else:
                    modules.update(alias.name for alias in node.names)
            elif node.module:
                modules.add(node.module)
    return modules


def _literal_strings(tree: ast.Module) -> set[str]:
    docstrings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(first.value.value)
    constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    return constants - docstrings


def test_base_package_contains_only_core_modules():
    """Requirement 1: the base itself contains no observation/action spaces."""
    modules = {path.name for path in PACKAGE_DIR.glob("*.py")}
    assert modules == EXPECTED_MODULES, f"unexpected base modules: {sorted(modules - EXPECTED_MODULES)}"
    for absent in ("adapters", "cli.py", "demo_page.html"):
        assert not (PACKAGE_DIR / absent).exists(), f"concrete adapter layer still inside the base: {absent}"


def test_core_does_not_import_concrete_io_or_model_libraries():
    for name in CORE_MODULES:
        modules = _imported_modules(_parse(name))
        roots = {module.split(".")[0] for module in modules}
        assert roots.isdisjoint(FORBIDDEN_IMPORT_ROOTS), f"{name} imports {sorted(roots & FORBIDDEN_IMPORT_ROOTS)}"
        for forbidden in FORBIDDEN_ADAPTER_MODULES:
            assert not any(module == forbidden or module.startswith(forbidden + ".") for module in modules), (
                f"{name} imports {forbidden}"
            )


def test_core_does_not_embed_concrete_action_or_observation_vocabulary():
    for name in CORE_MODULES:
        literals = _literal_strings(_parse(name))
        assert literals.isdisjoint(FORBIDDEN_LITERALS), f"{name} embeds {sorted(literals & FORBIDDEN_LITERALS)}"


def test_separation_helpers_catch_relative_adapter_imports():
    """The isolation check must not have a relative-import blind spot."""
    tree = ast.parse("from .adapters.playwright_io import PlaywrightIO")
    assert "nano_cua.adapters.playwright_io" in _imported_modules(tree)
    tree = ast.parse("from . import adapters")
    assert "nano_cua.adapters" in _imported_modules(tree)
    tree = ast.parse("from .contracts import Action")
    assert "nano_cua.contracts" in _imported_modules(tree)


def test_core_package_import_does_not_pull_in_adapters():
    """Importing nano_cua must not require playwright or httpx to be installed."""
    code = (
        "import sys, nano_cua; "
        "assert 'playwright' not in sys.modules, 'playwright imported'; "
        "assert 'httpx' not in sys.modules, 'httpx imported'; "
        "assert 'nano_cua.adapters' not in sys.modules, 'adapters imported'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PACKAGE_DIR.parents[1]),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
