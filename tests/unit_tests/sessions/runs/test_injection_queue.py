from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.enums import InjectionSource


def test_injection_manager_isolated_by_recipient() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.SUBAGENT,
        "m1",
        sender_instance_id="b1",
        sender_role_id="generalist",
    )
    mgr.enqueue(
        "run1",
        "a2",
        InjectionSource.SUBAGENT,
        "m2",
        sender_instance_id="b1",
        sender_role_id="generalist",
    )

    a1 = mgr.drain_at_boundary("run1", "a1")
    a2 = mgr.drain_at_boundary("run1", "a2")

    assert len(a1) == 1
    assert a1[0].content == "m1"
    assert len(a2) == 1
    assert a2[0].content == "m2"
