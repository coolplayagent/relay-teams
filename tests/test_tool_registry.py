import pytest

from agent_teams.tools.registry.defaults import build_default_registry


def test_registry_rejects_unknown_tools() -> None:
    registry = build_default_registry()
    with pytest.raises(ValueError):
        registry.validate_known(('read', 'unknown_tool'))
