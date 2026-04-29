# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.app import FrontendStaticFiles


def test_frontend_static_files_revalidate_browser_modules(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    js_dir = dist_dir / "js"
    js_dir.mkdir(parents=True)
    (js_dir / "app.js").write_text("export const ok = true;\n", encoding="utf-8")

    app = FastAPI()
    app.mount("/", FrontendStaticFiles(directory=str(dist_dir), html=True))

    with TestClient(app) as client:
        response = client.get("/js/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
