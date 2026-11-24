"""Static inspection of the installed Scrapling package, for the engine capability map.

It lists every public feature by reading Scrapling's source (never importing it), so a test can
fail when an upgrade adds a feature the map in ``rastro.engine_coverage`` does not mention.
"""

from __future__ import annotations

import ast
import importlib.util
import pkgutil
from pathlib import Path

__all__ = ["inspect_scrapling_features"]

_TYPED_DICTS = frozenset({"RequestsSession", "PlaywrightSession", "PlaywrightFetchParams"})


def _scrapling_root() -> Path:
    spec = importlib.util.find_spec("scrapling")
    if spec is None or spec.origin is None:
        raise RuntimeError("scrapling is not installed")
    return Path(spec.origin).parent


def _class_functions(tree: ast.Module, class_name: str) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(
                    item, ast.FunctionDef | ast.AsyncFunctionDef
                ) and not item.name.startswith("_"):
                    found.add(f"{class_name}.{item.name}")
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and not target.id.startswith("_"):
                            found.add(f"{class_name}.{target.id}")
    return found


def _class_targets(tree: ast.Module, class_names: set[str]) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in class_names:
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if not item.target.id.startswith("_"):
                        found.add(f"{node.name}.{item.target.id}")
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and not target.id.startswith("_"):
                            found.add(f"{node.name}.{target.id}")
    return found


def _mcp_tools(tree: ast.Module) -> set[str]:
    tools: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_tool"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and node.args[0].args
            and isinstance(node.args[0].args[0], ast.Attribute)
        ):
            tools.add(f"mcp.{node.args[0].args[0].attr}")
    return tools


def _read_ast(root: Path, rel: str) -> ast.Module:
    return ast.parse((root / rel).read_text(encoding="utf-8"))


def inspect_scrapling_features() -> set[str]:
    """Discover Scrapling's public features from source files without importing optional extras."""
    root = _scrapling_root()
    selector = _class_functions(_read_ast(root, "parser.py"), "Selector") | {
        f"Selector.{name.split('.', 1)[1]}"
        for name in _class_functions(_read_ast(root, "core/mixins.py"), "SelectorsGeneration")
    }
    templates = {
        f"templates.{mod.name}"
        for mod in pkgutil.iter_modules([str(root / "spiders" / "templates")])
        if not mod.name.startswith("_")
    }
    return (
        selector
        | _class_targets(_read_ast(root, "engines/_browsers/_types.py"), set(_TYPED_DICTS))
        | _class_targets(_read_ast(root, "spiders/spider.py"), {"Spider"})
        | templates
        | _mcp_tools(_read_ast(root, "core/ai/server.py"))
    )
