import pytest

from agent_teams.tools.workspace_tools.shell_policy import (
    MAX_TIMEOUT_SECONDS,
    normalize_timeout,
    validate_shell_command,
)


def test_shell_policy_allows_more_command() -> None:
    validate_shell_command("more README.md")


def test_shell_policy_rejects_empty_command() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_shell_command("   ")


def test_shell_timeout_normalization() -> None:
    assert normalize_timeout(None) > 0
    assert normalize_timeout(MAX_TIMEOUT_SECONDS + 99) == MAX_TIMEOUT_SECONDS
