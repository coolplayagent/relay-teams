import pytest

from agent_teams.tools.workspace.shell_policy import (
    MAX_TIMEOUT_SECONDS,
    normalize_timeout,
    validate_shell_command,
)


def test_shell_policy_blocks_dangerous_command() -> None:
    with pytest.raises(ValueError):
        validate_shell_command('rm -rf /')


def test_shell_timeout_normalization() -> None:
    assert normalize_timeout(None) > 0
    assert normalize_timeout(MAX_TIMEOUT_SECONDS + 99) == MAX_TIMEOUT_SECONDS
