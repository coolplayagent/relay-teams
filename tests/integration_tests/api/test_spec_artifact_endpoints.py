# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.interfaces.server.routers.tasks import router


def _collect_route_info() -> tuple[set[str], dict[str, set[str]]]:
    paths: set[str] = set()
    methods_by_path: dict[str, set[str]] = {}
    for route in router.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        paths.add(path)
        route_methods = getattr(route, "methods", set())
        methods_by_path.setdefault(path, set()).update(route_methods)
    return paths, methods_by_path


class TestSpecArtifactRouteDefinitions:
    """Verify the three new routes are registered on the tasks router."""

    def test_spec_artifacts_list_route_exists(self) -> None:
        paths, _ = _collect_route_info()
        assert "/tasks/{task_id}/spec-artifacts" in paths

    def test_spec_artifacts_list_route_is_get(self) -> None:
        _, methods = _collect_route_info()
        assert "GET" in methods.get("/tasks/{task_id}/spec-artifacts", set())

    def test_spec_artifact_diff_route_exists(self) -> None:
        paths, _ = _collect_route_info()
        assert "/tasks/{task_id}/spec-artifacts/{version}/diff" in paths

    def test_spec_artifact_diff_route_is_get(self) -> None:
        _, methods = _collect_route_info()
        assert "GET" in methods.get(
            "/tasks/{task_id}/spec-artifacts/{version}/diff", set()
        )

    def test_spec_checkpoint_evaluations_route_exists(self) -> None:
        paths, _ = _collect_route_info()
        assert "/tasks/{task_id}/spec-checkpoint-evaluations" in paths

    def test_spec_checkpoint_evaluations_route_is_get(self) -> None:
        _, methods = _collect_route_info()
        assert "GET" in methods.get(
            "/tasks/{task_id}/spec-checkpoint-evaluations", set()
        )
