# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.board.adapter import (
    BoardTaskState,
    TaskBoardAdapter,
)
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

# The active adapter -- set by the Coordinator when a board is configured.
# In production this lives on the Coordinator instance; the module-level
# variable serves as a simple default for tool functions.
_active_adapter: TaskBoardAdapter | None = None


def set_board_adapter(adapter: TaskBoardAdapter | None) -> None:
    """Set the active board adapter for controlled tools."""
    global _active_adapter
    _active_adapter = adapter


def get_board_adapter() -> TaskBoardAdapter:
    """Get the active board adapter, raising if none is configured."""
    if _active_adapter is None:
        raise RuntimeError("No board adapter is configured")
    return _active_adapter


async def board_add_comment(
    task_id: str,
    body: str,
) -> dict[str, object]:
    """Agent tool: add a comment to an external board task."""
    adapter = get_board_adapter()
    await adapter.add_comment(task_id=task_id, body=body)
    LOGGER.info("board comment added to %s", task_id)
    return {"commented": True, "task_id": task_id}


async def board_update_task(
    task_id: str,
    state: str | None = None,
    labels: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Agent tool: update an external board task state and/or labels."""
    adapter = get_board_adapter()
    if state is not None:
        board_state = BoardTaskState(state)
        await adapter.move_task(task_id=task_id, to_state=board_state)
    if labels is not None:
        LOGGER.info("label update requested for task %s: %s", task_id, labels)
    LOGGER.info("board task %s updated", task_id)
    return {"updated": True, "task_id": task_id}


async def board_link_pr(
    task_id: str,
    pr_url: str,
) -> dict[str, object]:
    """Agent tool: link a PR to a board task."""
    adapter = get_board_adapter()
    await adapter.add_artifact(task_id=task_id, name="Pull Request", url=pr_url)
    LOGGER.info("board PR linked to %s", task_id)
    return {"linked": True, "task_id": task_id, "pr_url": pr_url}


async def board_attach_evidence(
    task_id: str,
    evidence_type: str,
    content: str,
) -> dict[str, object]:
    """Agent tool: attach CI results / verification evidence to a board task."""
    adapter = get_board_adapter()
    await adapter.add_artifact(task_id=task_id, name=evidence_type, url=content)
    LOGGER.info("board evidence attached to %s", task_id)
    return {
        "attached": True,
        "task_id": task_id,
        "evidence_type": evidence_type,
    }
