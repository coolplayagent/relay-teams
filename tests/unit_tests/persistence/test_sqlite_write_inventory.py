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
    Path("src/relay_teams/agents/execution/message_repository.py"): "MessageRepository",
    Path(
        "src/relay_teams/agents/instances/instance_repository.py"
    ): "AgentInstanceRepository",
    Path("src/relay_teams/agents/tasks/task_repository.py"): "TaskRepository",
    Path(
        "src/relay_teams/automation/automation_bound_session_queue_repository.py"
    ): "AutomationBoundSessionQueueRepository",
    Path(
        "src/relay_teams/automation/automation_delivery_repository.py"
    ): "AutomationDeliveryRepository",
    Path(
        "src/relay_teams/automation/automation_event_repository.py"
    ): "AutomationEventRepository",
    Path(
        "src/relay_teams/automation/automation_repository.py"
    ): "AutomationProjectRepository",
    Path(
        "src/relay_teams/external_agents/session_repository.py"
    ): "ExternalAgentSessionRepository",
    Path("src/relay_teams/gateway/feishu/account_repository.py"): (
        "FeishuAccountRepository"
    ),
    Path("src/relay_teams/gateway/feishu/message_pool_repository.py"): (
        "FeishuMessagePoolRepository"
    ),
    Path(
        "src/relay_teams/gateway/gateway_session_repository.py"
    ): "GatewaySessionRepository",
    Path("src/relay_teams/gateway/wechat/account_repository.py"): (
        "WeChatAccountRepository"
    ),
    Path("src/relay_teams/gateway/wechat/inbound_queue_repository.py"): (
        "WeChatInboundQueueRepository"
    ),
    Path("src/relay_teams/gateway/xiaoluban/account_repository.py"): (
        "XiaolubanAccountRepository"
    ),
    Path("src/relay_teams/media/asset_repository.py"): "MediaAssetRepository",
    Path("src/relay_teams/metrics/stores/sqlite.py"): "SqliteMetricAggregateStore",
    Path("src/relay_teams/monitors/repository.py"): "MonitorRepository",
    Path("src/relay_teams/persistence/shared_state_repo.py"): "SharedStateRepository",
    Path("src/relay_teams/providers/token_usage_repo.py"): "TokenUsageRepository",
    Path("src/relay_teams/retrieval/sqlite_store.py"): "SqliteFts5RetrievalStore",
    Path("src/relay_teams/roles/memory_repository.py"): "RoleMemoryRepository",
    Path("src/relay_teams/roles/temporary_role_repository.py"): (
        "TemporaryRoleRepository"
    ),
    Path("src/relay_teams/sessions/external_session_binding_repository.py"): (
        "ExternalSessionBindingRepository"
    ),
    Path("src/relay_teams/sessions/runs/background_tasks/repository.py"): (
        "BackgroundTaskRepository"
    ),
    Path("src/relay_teams/sessions/runs/event_log.py"): "EventLog",
    Path("src/relay_teams/sessions/runs/run_intent_repo.py"): "RunIntentRepository",
    Path("src/relay_teams/sessions/runs/run_runtime_repo.py"): ("RunRuntimeRepository"),
    Path("src/relay_teams/sessions/runs/run_state_repo.py"): "RunStateRepository",
    Path("src/relay_teams/sessions/runs/todo_repository.py"): "TodoRepository",
    Path("src/relay_teams/sessions/runs/user_question_repository.py"): (
        "UserQuestionRepository"
    ),
    Path("src/relay_teams/sessions/session_history_marker_repository.py"): (
        "SessionHistoryMarkerRepository"
    ),
    Path("src/relay_teams/sessions/session_repository.py"): "SessionRepository",
    Path("src/relay_teams/tools/runtime/approval_ticket_repo.py"): (
        "ApprovalTicketRepository"
    ),
    Path("src/relay_teams/tools/workspace_tools/shell_approval_repo.py"): (
        "ShellApprovalRepository"
    ),
    Path("src/relay_teams/triggers/repository.py"): "TriggerRepository",
    Path("src/relay_teams/workspace/ssh_profile_repository.py"): (
        "SshProfileRepository"
    ),
    Path("src/relay_teams/workspace/workspace_repository.py"): "WorkspaceRepository",
}
_ALLOWED_OPEN_SQLITE_FILES = {
    Path("src/relay_teams/persistence/db.py"),
    Path("src/relay_teams/persistence/sqlite_repository.py"),
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


def test_sqlite_repositories_do_not_open_connections_directly() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_REPO_ROOT)
        if relative_path in _ALLOWED_OPEN_SQLITE_FILES:
            continue
        if "open_sqlite(" in path.read_text(encoding="utf-8"):
            offenders.append(relative_path.as_posix())

    assert offenders == []


def test_sqlite_repository_public_methods_have_async_interfaces() -> None:
    missing: list[str] = []
    for relative_path, class_name in _SHARED_SQLITE_WRITERS.items():
        module_path = _REPO_ROOT / relative_path
        missing.extend(
            _missing_public_async_methods(module_path, class_name=class_name)
        )

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


def _missing_public_async_methods(path: Path, *, class_name: str) -> list[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return _missing_public_async_methods_from_class(
                node,
                path=path,
                class_name=class_name,
            )
    raise AssertionError(f"Class not found: {class_name} in {path}")


def _missing_public_async_methods_from_class(
    node: ast.ClassDef,
    *,
    path: Path,
    class_name: str,
) -> list[str]:
    async_methods = {
        item.name for item in node.body if isinstance(item, ast.AsyncFunctionDef)
    }
    missing: list[str] = []
    for item in node.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        if item.name == "__init__" or item.name.startswith("_"):
            continue
        if item.decorator_list:
            continue
        async_name = f"{item.name}_async"
        if async_name not in async_methods:
            relative_path = path.relative_to(_REPO_ROOT).as_posix()
            missing.append(f"{relative_path}::{class_name}.{item.name}")
    return missing
