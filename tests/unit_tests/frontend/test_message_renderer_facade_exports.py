# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_message_renderer_facade_re_exports_overlay_binding_helper() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "messageRenderer.js"
    ).read_text(encoding="utf-8")

    assert "bindStreamOverlayToContainer" in source


def test_message_renderer_facade_re_exports_message_copy_helpers() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "messageRenderer.js"
    ).read_text(encoding="utf-8")

    assert "bindCopyButton" in source
    assert "syncLastAnswerCopyButton" in source
    assert "extractMessageCopyText" in source
