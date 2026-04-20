# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

from relay_teams.sessions.runs.user_question_manager import (
    UserQuestionClosedError,
    UserQuestionManager,
)


def test_mark_question_closed_unblocks_waiters() -> None:
    manager = UserQuestionManager()
    manager.open_question(
        run_id="run-1",
        question_id="question-1",
        instance_id="inst-1",
        role_id="Coordinator",
    )
    completed = threading.Event()
    captured: list[str] = []

    def waiter() -> None:
        try:
            manager.wait_for_answer(
                run_id="run-1",
                question_id="question-1",
                timeout=1.0,
            )
        except UserQuestionClosedError as exc:
            captured.append(str(exc))
        finally:
            completed.set()

    thread = threading.Thread(target=waiter)
    thread.start()

    manager.mark_question_closed(
        run_id="run-1",
        question_id="question-1",
        reason="run_stopped",
    )

    assert completed.wait(timeout=1.0) is True
    thread.join(timeout=1.0)
    assert captured
    assert "run_stopped" in captured[0]
    assert manager.get_question(run_id="run-1", question_id="question-1") is None
