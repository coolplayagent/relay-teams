# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.env.env_cli import (
    is_sensitive_env_key,
    merge_env_source,
    truncate_for_table,
)


def test_merge_env_source_tracks_latest_source() -> None:
    merged: dict[str, str] = {"TOKEN": "old"}
    source_by_key: dict[str, str] = {"TOKEN": "process"}

    merge_env_source(merged, source_by_key, {"TOKEN": "new"}, "app")

    assert merged == {"TOKEN": "new"}
    assert source_by_key == {"TOKEN": "app"}


def test_truncate_for_table_preserves_short_values() -> None:
    assert truncate_for_table("short", 10) == "short"


def test_truncate_for_table_shortens_long_values() -> None:
    assert truncate_for_table("abcdefghijklmnopqrstuvwxyz", 8) == "abcde..."


def test_is_sensitive_env_key_delegates_sensitive_key_detection() -> None:
    assert is_sensitive_env_key("SERVICE_TOKEN") is True
    assert is_sensitive_env_key("SERVICE_URL") is False
