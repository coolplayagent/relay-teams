from pathlib import Path

from agent_teams.roles.registry import RoleLoader


def test_coordinator_uses_workflow_tools_and_not_emit_event() -> None:
    registry = RoleLoader().load_all(Path('roles'))
    coordinator = registry.get('coordinator_agent')
    tools = set(coordinator.tools)

    assert 'create_workflow_graph' in tools
    assert 'materialize_code_shards_from_design' in tools
    assert 'dispatch_ready_tasks' in tools
    assert 'get_workflow_status' in tools
    assert 'emit_event' not in tools
