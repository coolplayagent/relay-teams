from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskSpecStrictness(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskSpecSyncStatus(str, Enum):
    UNKNOWN = "unknown"
    IN_SYNC = "in_sync"
    SPEC_AHEAD = "spec_ahead"
    CODE_AHEAD = "code_ahead"
    NEEDS_REVIEW = "needs_review"


class TaskTimeoutAction(str, Enum):
    FAIL = "fail"
    RETRY = "retry"
    HUMAN_GATE = "human_gate"


class VerificationLayer(str, Enum):
    STRUCTURE = "structure"
    BEHAVIOR = "behavior"
    EVIDENCE = "evidence"
    SEMANTIC = "semantic"
    SPEC = "spec"
    CONTRACT = "contract"
    SECURITY = "security"
    FORMAL = "formal"


class VerificationEvidenceKind(str, Enum):
    TASK_RESULT = "task_result"
    REQUIRED_FILE = "required_file"
    COMMAND = "command"
    TEST_RESULT = "test_result"
    LINT_RESULT = "lint_result"
    DIFF_SUMMARY = "diff_summary"
    FORMAL_PROOF = "formal_proof"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    GATE_FINDING = "gate_finding"
    RUNTIME_GUARDRAIL_REPORT = "runtime_guardrail_report"
    TIMEOUT_HANDOFF = "timeout_handoff"


class VerificationEvidenceTarget(str, Enum):
    ACCEPTANCE_CRITERION = "acceptance_criterion"
    EVIDENCE_EXPECTATION = "evidence_expectation"


class FormalVerificationLanguage(str, Enum):
    TLA_PLUS = "tla_plus"
    ALLOY = "alloy"
    LEAN = "lean"
    COQ = "coq"
    ISABELLE = "isabelle"
    CUSTOM = "custom"


class FormalVerificationToolProfile(str, Enum):
    TLC = "tlc"
    ALLOY_ANALYZER = "alloy_analyzer"
    LEAN = "lean"
    COQ = "coq"
    ISABELLE = "isabelle"
    CUSTOM = "custom"


class EvaluationAggregation(str, Enum):
    MAJORITY = "majority"
    UNANIMOUS = "unanimous"
    WEIGHTED = "weighted"


class WakeupStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    EXPIRED = "expired"


class WakeupReason(str, Enum):
    TIMEOUT_RETRY = "timeout_retry"
    TASK_COMPLETED = "task_completed"
    APPROVAL_PASSED = "approval_passed"
    USER_INPUT = "user_input"
    DEPENDENCY_RESOLVED = "dependency_resolved"
    ORPHAN_RECOVERY = "orphan_recovery"


class TaskArtifactPhase(str, Enum):
    SPEC = "spec"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    DELIVERY = "delivery"
