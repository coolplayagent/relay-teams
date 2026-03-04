from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.core.enums import InstanceStatus


def test_instance_lifecycle() -> None:
    pool = InstancePool()
    instance = pool.create_subagent('generalist')
    assert instance.status == InstanceStatus.IDLE

    pool.mark_running(instance.instance_id)
    assert pool.get(instance.instance_id).status == InstanceStatus.RUNNING

    pool.mark_timeout(instance.instance_id)
    assert pool.get(instance.instance_id).status == InstanceStatus.TIMEOUT
