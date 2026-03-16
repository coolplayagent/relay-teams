from pathlib import Path

from agent_teams.roles.role_registry import RoleLoader


def test_role_loader_loads_markdown_role() -> None:
    registry = RoleLoader().load_all(Path(".agent_teams/roles"))
    roles = registry.list_roles()
    assert len(roles) >= 1
    assert roles[0].role_id
