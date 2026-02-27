from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_teams.core.enums import ScopeType, TaskStatus
from agent_teams.core.models import ScopeRef, StateMutation, TaskRecord
from agent_teams.state.shared_store import SharedStore

WORKFLOW_GRAPH_KEY = 'workflow_graph'
MODULE_PLAN_PATTERN = re.compile(r'```json\s*(?P<body>.*?)```', re.DOTALL | re.IGNORECASE)

StageName = Literal['spec', 'design', 'code', 'verify']


@dataclass(frozen=True)
class ModulePlanItem:
    module_id: str
    files: tuple[str, ...]
    complexity: str
    scope: str


def workflow_scope(task_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)


def load_graph(store: SharedStore, *, task_id: str) -> dict[str, object] | None:
    raw = store.get_state(workflow_scope(task_id), WORKFLOW_GRAPH_KEY)
    if not raw:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('workflow_graph must be a json object')
    return value


def save_graph(store: SharedStore, *, task_id: str, graph: dict[str, object]) -> None:
    store.manage_state(
        StateMutation(
            scope=workflow_scope(task_id),
            key=WORKFLOW_GRAPH_KEY,
            value_json=json.dumps(graph, ensure_ascii=False),
        )
    )


def stage_tag(stage: StageName) -> str:
    return f'stage:{stage}'


def workflow_tag(workflow_id: str) -> str:
    return f'workflow:{workflow_id}'


def module_tag(module_id: str) -> str:
    return f'module:{module_id}'


def parse_module_plan(content: str) -> tuple[ModulePlanItem, ...]:
    for match in MODULE_PLAN_PATTERN.finditer(content):
        body = match.group('body').strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        parsed = _parse_module_payload(payload)
        if parsed:
            return parsed
    raise ValueError('MODULE_PLAN json block not found in design document')


def _parse_module_payload(payload: object) -> tuple[ModulePlanItem, ...]:
    if not isinstance(payload, dict):
        return ()
    modules = payload.get('modules')
    if not isinstance(modules, list) or not modules:
        return ()

    parsed: list[ModulePlanItem] = []
    for item in modules:
        if not isinstance(item, dict):
            continue
        module_id = item.get('module_id')
        files = item.get('files')
        complexity = item.get('complexity', 'M')
        scope = item.get('scope', '')
        if not isinstance(module_id, str) or not module_id.strip():
            continue
        if not isinstance(files, list) or not files:
            continue
        file_items = tuple(str(file).strip() for file in files if str(file).strip())
        if not file_items:
            continue
        complexity_text = str(complexity).strip().upper() or 'M'
        if complexity_text not in {'S', 'M', 'L'}:
            complexity_text = 'M'
        parsed.append(
            ModulePlanItem(
                module_id=module_id.strip(),
                files=file_items,
                complexity=complexity_text,
                scope=str(scope).strip(),
            )
        )
    return tuple(parsed)


def node_ready(*, node_depends_on: tuple[str, ...], task_map: dict[str, TaskRecord]) -> bool:
    for dep_id in node_depends_on:
        dep = task_map.get(dep_id)
        if dep is None:
            return False
        if dep.status != TaskStatus.COMPLETED:
            return False
    return True


def stage_from_scope(scope: tuple[str, ...]) -> str | None:
    for item in scope:
        if item.startswith('stage:'):
            return item.split(':', 1)[1]
    return None


def design_doc_path(*, workspace_root: Path, run_id: str) -> Path:
    return workspace_root / '.agent_teams' / 'stage_docs' / run_id / 'design.md'
