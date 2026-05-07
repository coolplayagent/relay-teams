# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_source_tree_does_not_reintroduce_sync_net_http_clients() -> None:
    src_root = Path(__file__).parents[3] / "src" / "relay_teams"
    forbidden_tokens = (
        "create_sync_http_client",
        "create_runtime_sync_http_client",
        "SyncProxyRoutingTransport",
    )

    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden_tokens):
            offenders.append(str(path.relative_to(src_root)))

    assert offenders == []
