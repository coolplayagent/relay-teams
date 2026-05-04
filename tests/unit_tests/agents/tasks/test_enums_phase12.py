# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.enums import (
    VerificationEvidenceKind,
    WakeupReason,
)


class TestNewEnums:
    def test_wakeup_reason_values(self) -> None:
        assert WakeupReason.TIMEOUT_RETRY == "timeout_retry"
        assert WakeupReason.TASK_COMPLETED == "task_completed"
        assert WakeupReason.APPROVAL_PASSED == "approval_passed"
        assert WakeupReason.USER_INPUT == "user_input"
        assert WakeupReason.DEPENDENCY_RESOLVED == "dependency_resolved"
        assert WakeupReason.ORPHAN_RECOVERY == "orphan_recovery"

    def test_timeout_handoff_evidence_kind(self) -> None:
        assert VerificationEvidenceKind.TIMEOUT_HANDOFF == "timeout_handoff"
