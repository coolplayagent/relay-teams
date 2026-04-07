# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.release import (
    build_timestamp_version,
    render_version_file,
    write_version_file,
)


def test_build_timestamp_version_uses_expected_format() -> None:
    assert build_timestamp_version("20260326091530") == "0.0.3.20260326091530"


def test_build_timestamp_version_rejects_non_digit_timestamp() -> None:
    with pytest.raises(ValueError, match="digits only"):
        build_timestamp_version("2026-03-26T09:15:30")


def test_write_version_file_persists_expected_module_contents(tmp_path: Path) -> None:
    output_path = tmp_path / "_version.py"

    write_version_file("0.0.3.20260326091530", output_path)

    assert output_path.read_text(encoding="utf-8") == render_version_file(
        "0.0.3.20260326091530"
    )
