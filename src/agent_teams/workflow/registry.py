# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import yaml

from agent_teams.workflow.spec import (
    WorkflowDefinition,
    WorkflowTaskTemplate,
)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: list[WorkflowDefinition] = []

    def register(self, workflow: WorkflowDefinition) -> None:
        for index, existing in enumerate(self._workflows):
            if existing.workflow_id == workflow.workflow_id:
                self._workflows[index] = workflow
                return
        self._workflows.append(workflow)

    def get(self, workflow_id: str) -> WorkflowDefinition:
        for workflow in self._workflows:
            if workflow.workflow_id == workflow_id:
                return workflow
        raise KeyError(f"Unknown workflow_id: {workflow_id}")

    def list_workflows(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._workflows)

    def recommend(self, objective: str) -> WorkflowDefinition | None:
        if not self._workflows:
            return None
        normalized_objective = objective.strip().lower()
        default_workflow = next(
            (workflow for workflow in self._workflows if workflow.is_default),
            None,
        )
        if not normalized_objective:
            return default_workflow or self._workflows[0]

        scored = sorted(
            self._workflows,
            key=lambda workflow: self._score(workflow, normalized_objective),
            reverse=True,
        )
        best = scored[0]
        best_score = self._score(best, normalized_objective)
        if best_score[0] > 0:
            return best
        return default_workflow or best

    def _score(self, workflow: WorkflowDefinition, objective: str) -> tuple[int, int]:
        hits = 0
        for hint in workflow.selection_hints:
            normalized_hint = hint.strip().lower()
            if normalized_hint and normalized_hint in objective:
                hits += 1
        default_bonus = 1 if workflow.is_default else 0
        return hits, default_bonus


class WorkflowLoader:
    REQUIRED_FIELDS = ("workflow_id", "name", "version", "tasks")

    def load_all(self, workflows_dir: Path) -> WorkflowRegistry:
        registry = WorkflowRegistry()
        if not workflows_dir.exists():
            return registry
        for md_file in sorted(workflows_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        self._validate_defaults(registry)
        return registry

    def load_one(self, path: Path) -> WorkflowDefinition:
        raw = path.read_text(encoding="utf-8")
        front_matter, body = self._split_front_matter(raw)
        parsed = yaml.safe_load(front_matter)
        if not isinstance(parsed, dict):
            raise ValueError(f"Invalid front matter for workflow file: {path}")

        missing = [field for field in self.REQUIRED_FIELDS if field not in parsed]
        if missing:
            raise ValueError(f"Missing fields in {path}: {missing}")

        selection_hints = parsed.get("selection_hints", [])
        if selection_hints is None:
            selection_hints = []
        if not isinstance(selection_hints, list):
            raise ValueError(f"selection_hints must be a list in {path}")

        raw_tasks = parsed.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError(f"tasks must be a non-empty list in {path}")

        tasks: list[WorkflowTaskTemplate] = []
        for entry in raw_tasks:
            if not isinstance(entry, dict):
                raise ValueError(f"workflow task entries must be objects in {path}")
            depends_on = entry.get("depends_on", [])
            if depends_on is None:
                depends_on = []
            if not isinstance(depends_on, list):
                raise ValueError(f"workflow task depends_on must be a list in {path}")
            tasks.append(
                WorkflowTaskTemplate(
                    task_name=str(entry.get("task_name", "")),
                    role_id=str(entry.get("role_id", "")),
                    objective_template=str(entry.get("objective_template", "")),
                    depends_on=tuple(str(item) for item in depends_on),
                )
            )

        description = str(parsed.get("description", "")).strip()
        guidance = body.strip()
        if guidance and not description:
            description = guidance.splitlines()[0].strip()

        return WorkflowDefinition(
            workflow_id=str(parsed["workflow_id"]),
            name=str(parsed["name"]),
            version=str(parsed["version"]),
            description=description,
            selection_hints=tuple(str(item) for item in selection_hints),
            is_default=bool(parsed.get("is_default", False)),
            tasks=tuple(tasks),
            guidance=guidance,
        )

    def _split_front_matter(self, content: str) -> tuple[str, str]:
        content = content.lstrip("\ufeff")
        if not content.startswith("---"):
            raise ValueError("Workflow markdown must start with YAML front matter")

        lines = content.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("Workflow markdown must start with YAML front matter")

        end_index: int | None = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end_index = index
                break

        if end_index is None:
            raise ValueError("Invalid YAML front matter delimiters")

        front_matter = "".join(lines[1:end_index])
        body = "".join(lines[end_index + 1 :])
        return front_matter, body

    def _validate_defaults(self, registry: WorkflowRegistry) -> None:
        defaults = [
            workflow.workflow_id
            for workflow in registry.list_workflows()
            if workflow.is_default
        ]
        if len(defaults) > 1:
            raise ValueError(
                f"Only one workflow can be marked is_default=true, got {defaults}"
            )
