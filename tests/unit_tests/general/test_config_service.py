# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.general import GeneralConfigUpdate
from relay_teams.general.config_service import GeneralConfigService


def test_general_config_service_returns_default_when_file_is_missing(
    tmp_path: Path,
) -> None:
    service = GeneralConfigService(config_dir=tmp_path)

    config = service.get_config()

    assert config.shell_safety_policy_enabled is True


@pytest.mark.parametrize(
    "content",
    (
        "{not-json",
        "[]",
        '{"unknown": true}',
    ),
)
def test_general_config_service_returns_default_for_invalid_saved_config(
    tmp_path: Path,
    content: str,
) -> None:
    config_file = tmp_path / "general.json"
    config_file.write_text(content, encoding="utf-8")
    service = GeneralConfigService(config_dir=tmp_path)

    config = service.get_config()

    assert config.shell_safety_policy_enabled is True


def test_general_config_service_saves_and_reads_shell_policy(tmp_path: Path) -> None:
    service = GeneralConfigService(config_dir=tmp_path)

    config = service.save_config(GeneralConfigUpdate(shell_safety_policy_enabled=False))

    assert config.shell_safety_policy_enabled is False
    assert service.get_config().shell_safety_policy_enabled is False
    assert (tmp_path / "general.json").read_text(encoding="utf-8") == (
        '{\n  "shell_safety_policy_enabled": false\n}\n'
    )
