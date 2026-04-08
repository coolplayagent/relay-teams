# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.release.frontend_packaging import copy_frontend_dist


def test_copy_frontend_dist_copies_frontend_assets_into_package_tree(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    source_dir = project_root / "frontend" / "dist"
    (source_dir / "js").mkdir(parents=True)
    (source_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (source_dir / "js" / "app.js").write_text("console.log('ready');", encoding="utf-8")
    build_lib = tmp_path / "build-lib"

    copied_dir = copy_frontend_dist(project_root=project_root, build_lib=build_lib)

    assert copied_dir == build_lib / "relay_teams" / "frontend" / "dist"
    assert (copied_dir / "index.html").read_text(encoding="utf-8") == "<html></html>"
    assert (copied_dir / "js" / "app.js").read_text(encoding="utf-8") == (
        "console.log('ready');"
    )


def test_copy_frontend_dist_replaces_stale_assets(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    source_dir = project_root / "frontend" / "dist"
    source_dir.mkdir(parents=True)
    (source_dir / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
    build_lib = tmp_path / "build-lib"
    target_dir = build_lib / "relay_teams" / "frontend" / "dist"
    target_dir.mkdir(parents=True)
    (target_dir / "stale.txt").write_text("stale", encoding="utf-8")

    copied_dir = copy_frontend_dist(project_root=project_root, build_lib=build_lib)

    assert copied_dir == target_dir
    assert not (target_dir / "stale.txt").exists()
    assert (target_dir / "index.html").read_text(encoding="utf-8") == (
        "<html>fresh</html>"
    )


def test_manifest_includes_frontend_dist_for_source_distributions() -> None:
    manifest_path = Path(__file__).resolve().parents[3] / "MANIFEST.in"

    manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()

    assert "graft frontend/dist" in manifest_lines
