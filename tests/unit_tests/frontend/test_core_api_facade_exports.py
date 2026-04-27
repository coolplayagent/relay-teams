# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_core_api_facade_re_exports_session_subagent_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "core" / "api.js").read_text(
        encoding="utf-8"
    )

    assert "fetchSessionSubagents" in source
    assert "deleteSessionSubagent" in source


def test_core_api_facade_re_exports_model_catalog_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "core" / "api.js").read_text(
        encoding="utf-8"
    )

    assert "fetchModelCatalog" in source
    assert "refreshModelCatalog" in source


def test_core_api_facade_re_exports_xiaoluban_im_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "core" / "api.js").read_text(
        encoding="utf-8"
    )

    assert "updateXiaolubanGatewayImConfig" in source
    assert "fetchXiaolubanGatewayImForwardingCommand" in source
