# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "relay_teams"


def test_env_does_not_depend_on_net() -> None:
    _assert_no_forbidden_imports(
        SRC_ROOT / "env",
        _is_net_import,
        "env modules must not import relay_teams.net",
        candidate_text="relay_teams.net",
    )


def test_net_does_not_depend_on_tools() -> None:
    _assert_no_forbidden_imports(
        SRC_ROOT / "net",
        _is_tools_import,
        "net modules must not import relay_teams.tools",
        candidate_text="relay_teams.tools",
    )


def test_domain_modules_do_not_import_server_interface_layer() -> None:
    files = tuple(
        path
        for path in _iter_python_files(SRC_ROOT)
        if path.relative_to(SRC_ROOT).parts[0] != "interfaces"
    )
    _assert_no_forbidden_imports_in_files(
        files,
        _is_server_interface_import,
        "non-interface modules must not import relay_teams.interfaces.server",
        candidate_text="relay_teams.interfaces.server",
    )


def test_server_routers_do_not_import_repositories_directly() -> None:
    _assert_no_forbidden_imports(
        SRC_ROOT / "interfaces" / "server" / "routers",
        _is_repository_import,
        "server routers must go through services, not repositories",
        candidate_text="repository",
    )


def test_sessions_do_not_depend_on_gateway() -> None:
    _assert_no_forbidden_imports(
        SRC_ROOT / "sessions",
        _is_gateway_import,
        "sessions must not import relay_teams.gateway",
        candidate_text="relay_teams.gateway",
    )


def test_gateway_does_not_depend_on_server_container() -> None:
    _assert_no_forbidden_imports(
        SRC_ROOT / "gateway",
        _is_server_interface_import,
        "gateway modules must not import relay_teams.interfaces.server",
        candidate_text="relay_teams.interfaces.server",
    )


def test_package_exports_do_not_use_implicit_lazy_imports() -> None:
    violations: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        if path.name != "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
                violations.append(f"{path.relative_to(REPO_ROOT)} defines __getattr__")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_LAZY_IMPORTS":
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)} defines _LAZY_IMPORTS"
                        )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "_LAZY_IMPORTS"
            ):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} defines _LAZY_IMPORTS"
                )
        if "importlib.import_module" in source:
            violations.append(
                f"{path.relative_to(REPO_ROOT)} uses importlib.import_module"
            )
    assert not violations, (
        "package __init__.py exports must use explicit imports:\n"
        + "\n".join(sorted(violations))
    )


def _assert_no_forbidden_imports(
    scope: Path,
    is_forbidden: Callable[[str], bool],
    message: str,
    *,
    candidate_text: str | None = None,
) -> None:
    _assert_no_forbidden_imports_in_files(
        tuple(_iter_python_files(scope)),
        is_forbidden,
        message,
        candidate_text=candidate_text,
    )


def _assert_no_forbidden_imports_in_files(
    files: tuple[Path, ...],
    is_forbidden: Callable[[str], bool],
    message: str,
    *,
    candidate_text: str | None = None,
) -> None:
    violations: list[str] = []
    for path in files:
        if candidate_text is not None and candidate_text not in path.read_text(
            encoding="utf-8"
        ):
            continue
        for module in _imported_modules(path):
            if is_forbidden(module):
                violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    assert not violations, f"{message}:\n" + "\n".join(sorted(violations))


def _iter_python_files(scope: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(path for path in scope.rglob("*.py") if "__pycache__" not in path.parts)
    )


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


def _is_net_import(module: str) -> bool:
    return module == "relay_teams.net" or module.startswith("relay_teams.net.")


def _is_tools_import(module: str) -> bool:
    return module == "relay_teams.tools" or module.startswith("relay_teams.tools.")


def _is_server_interface_import(module: str) -> bool:
    return module == "relay_teams.interfaces.server" or module.startswith(
        "relay_teams.interfaces.server."
    )


def _is_gateway_import(module: str) -> bool:
    return module == "relay_teams.gateway" or module.startswith("relay_teams.gateway.")


def _is_repository_import(module: str) -> bool:
    if not module.startswith("relay_teams."):
        return False
    return any(
        part == "repository" or part.endswith("_repository")
        for part in module.split(".")
    )
