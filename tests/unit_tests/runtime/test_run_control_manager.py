import asyncio

import pytest

from agent_teams.runtime.run_control_manager import RunControlManager


def test_request_run_stop_cancels_run_task() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _worker() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(_worker())
        mgr.register_run_task(run_id='run-1', session_id='session-1', task=task)
        assert mgr.request_run_stop('run-1') is True
        await asyncio.sleep(0)
        assert task.cancelled()
        assert mgr.is_run_stop_requested('run-1') is True

    asyncio.run(_case())


def test_request_subagent_stop_marks_paused_context() -> None:
    async def _case() -> None:
        mgr = RunControlManager()

        async def _subagent() -> str:
            await asyncio.sleep(10)
            return 'x'

        task = asyncio.create_task(_subagent())
        mgr.register_instance_task(
            run_id='run-1',
            session_id='session-1',
            instance_id='inst-1',
            role_id='generalist',
            task_id='task-1',
            task=task,
        )

        paused = mgr.request_subagent_stop(run_id='run-1', instance_id='inst-1')
        assert paused is not None
        assert paused.session_id == 'session-1'
        assert paused.instance_id == 'inst-1'
        assert paused.task_id == 'task-1'
        await asyncio.sleep(0)
        assert task.cancelled()
        assert mgr.is_subagent_stop_requested(run_id='run-1', instance_id='inst-1') is True
        assert mgr.is_subagent_paused(session_id='session-1', instance_id='inst-1') is True

    asyncio.run(_case())


def test_release_paused_subagent_clears_blocking() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id='session-1',
        run_id='run-1',
        instance_id='inst-1',
        role_id='generalist',
        task_id='task-1',
    )
    released = mgr.release_paused_subagent(session_id='session-1', instance_id='inst-1')
    assert released is not None
    assert mgr.get_paused_subagent('session-1') is None
    assert mgr.is_subagent_stop_requested(run_id='run-1', instance_id='inst-1') is False


def test_context_raises_when_cancelled() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id='session-1',
        run_id='run-1',
        instance_id='inst-1',
        role_id='generalist',
        task_id='task-1',
    )
    ctx = mgr.context(run_id='run-1', instance_id='inst-1')
    with pytest.raises(asyncio.CancelledError):
        ctx.raise_if_cancelled()


def test_session_guard_blocks_main_input_when_paused() -> None:
    mgr = RunControlManager()
    mgr.pause_subagent(
        session_id='session-1',
        run_id='run-1',
        instance_id='inst-1',
        role_id='generalist',
        task_id='task-1',
    )
    with pytest.raises(RuntimeError):
        mgr.assert_session_allows_main_input('session-1')
