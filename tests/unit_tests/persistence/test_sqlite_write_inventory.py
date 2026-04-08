# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "relay_teams"
_ALLOWED_COMMIT_FILES = {
    Path("src/relay_teams/persistence/db.py"),
}
_SHARED_SQLITE_WRITERS = {
    Path(
        "src/relay_teams/automation/automation_event_repository.py"
    ): "AutomationEventRepository",
    Path(
        "src/relay_teams/automation/automation_repository.py"
    ): "AutomationProjectRepository",
    Path(
        "src/relay_teams/gateway/gateway_session_repository.py"
    ): "GatewaySessionRepository",
    Path("src/relay_teams/metrics/stores/sqlite.py"): "SqliteMetricAggregateStore",
    Path("src/relay_teams/persistence/shared_state_repo.py"): "SharedStateRepository",
    Path("src/relay_teams/providers/token_usage_repo.py"): "TokenUsageRepository",
    Path("src/relay_teams/retrieval/sqlite_store.py"): "SqliteFts5RetrievalStore",
    Path("src/relay_teams/roles/memory_repository.py"): "RoleMemoryRepository",
    Path("src/relay_teams/sessions/runs/run_intent_repo.py"): "RunIntentRepository",
    Path("src/relay_teams/sessions/session_repository.py"): "SessionRepository",
}


def test_only_db_helper_commits_transactions() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_REPO_ROOT)
        if relative_path in _ALLOWED_COMMIT_FILES:
            continue
        if ".commit(" in path.read_text(encoding="utf-8"):
            offenders.append(relative_path.as_posix())

    assert offenders == []


def test_shared_sqlite_writers_inherit_shared_repository_base() -> None:
    missing: list[str] = []
    for relative_path, class_name in _SHARED_SQLITE_WRITERS.items():
        module_path = _REPO_ROOT / relative_path
        class_bases = _class_bases_from_file(module_path, class_name=class_name)
        if "SharedSqliteRepository" not in class_bases:
            missing.append(f"{relative_path.as_posix()}::{class_name}")

    assert missing == []


def _class_bases_from_file(path: Path, *, class_name: str) -> tuple[str, ...]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(_base_name(base) for base in node.bases)
    raise AssertionError(f"Class not found: {class_name} in {path}")


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ast.unparse(base)
