"""Static checks that the core stays free of concrete I/O and action semantics.

Acceptance item: core files must not import playright / a VLM SDK nor encode
concrete observation or action vocabulary. Only the composition root and the
adapters are allowed to know those details.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import nano_cua

PACKAGE_DIR = Path(nano_cua.__file__).parent
CORE_MODULES = ("contracts.py", "runner.py", "policy.py", "mock.py")
FORBIDDEN_IMPORT_ROOTS = {"playwright", "httpx", "openai", "anthropic", "requests", "aiohttp", "selenium"}
FORBIDDEN_ADAPTER_MODULES = ("nano_cua.adapters", "nano_cua.cli")
FORBIDDEN_LITERALS = {"click", "type_text", "press_key", "screenshot", "keyboard", "mouse"}


def _parse(name: str) -> ast.Module:
    return ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
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
    )
    assert completed.returncode == 0, completed.stderr
