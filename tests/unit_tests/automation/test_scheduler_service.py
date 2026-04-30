from __future__ import annotations

import asyncio
from datetime import datetime
from typing import cast

from relay_teams.automation.automation_service import AutomationService
from relay_teams.automation.scheduler_service import AutomationSchedulerService


class _FakeAutomationService:
    def __init__(self) -> None:
        self.stop_event: asyncio.Event | None = None
        self.processed_at: list[datetime] = []

    async def process_due_projects_async(
        self,
        *,
        now: datetime,
    ) -> tuple[str, ...]:
        self.processed_at.append(now)
        if self.stop_event is not None:
            self.stop_event.set()
        return ()


def test_scheduler_run_loop_uses_async_automation_service() -> None:
    fake_service = _FakeAutomationService()
    scheduler = AutomationSchedulerService(
        automation_service=cast(AutomationService, fake_service),
        poll_interval_seconds=0.01,
    )
    fake_service.stop_event = scheduler._stop_event

    async def exercise() -> None:
        await scheduler._run_loop()

    asyncio.run(exercise())

    assert len(fake_service.processed_at) == 1
