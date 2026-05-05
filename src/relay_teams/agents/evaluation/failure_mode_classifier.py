# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)
from relay_teams.agents.evaluation.run_sampling_service import SampledRun
from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.agents.tasks.models import TaskSpec, VerificationReport
from relay_teams.logger import get_logger, log_event
from relay_teams.memory.service import MemoryBankService
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.validation import RequiredIdentifierStr

_LOGGER = get_logger(__name__)

_CLASSIFICATION_EVENT_TYPES: tuple[str, ...] = (
    RunEventType.TOOL_CALL.value,
    RunEventType.TOOL_RESULT.value,
    RunEventType.TOOL_APPROVAL_REQUESTED.value,
    RunEventType.TOOL_APPROVAL_RESOLVED.value,
    RunEventType.RUNTIME_GUARDRAIL_ALERT.value,
    RunEventType.RUNTIME_GUARDRAIL_REPORT.value,
    RunEventType.SPEC_CHECKPOINT_EVALUATED.value,
    RunEventType.SPEC_CHECKPOINT_APPLIED.value,
    RunEventType.TOKEN_USAGE.value,
    RunEventType.RUN_COMPLETED.value,
    RunEventType.RUN_FAILED.value,
)


class ClassificationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: RequiredIdentifierStr = Field(min_length=1)
    classifications: tuple[FailureModeClassification, ...] = ()
    errors: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime
    total_runs: int = Field(ge=0)
    classified_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)


class FailureModeClassifier:
    def __init__(
        self,
        *,
        llm_evaluator: LLMEvaluator,
        event_log: EventLog,
        memory_bank_service: MemoryBankService,
        classifier_version: str = "1.0.0",
        tool_count_threshold: int = 100,
        unique_tool_threshold: int = 15,
        token_usage_threshold: int = 200_000,
        approval_deny_rate_threshold: float = 0.5,
    ) -> None:
        self._llm_evaluator = llm_evaluator
        self._event_log = event_log
        self._memory_bank_service = memory_bank_service
        self._classifier_version = classifier_version
        self._tool_count_threshold = tool_count_threshold
        self._unique_tool_threshold = unique_tool_threshold
        self._token_usage_threshold = token_usage_threshold
        self._approval_deny_rate_threshold = approval_deny_rate_threshold

    async def classify_run(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        role_id: str | None = None,
        task_spec: TaskSpec | None = None,
        verification_report: VerificationReport | None = None,
    ) -> FailureModeClassification:
        evidence_parts: list[str] = []
        evidence_refs: list[str] = []
        detected_modes: dict[FailureMode, float] = {}

        # 1. Gather events for the run
        events = await self._event_log.list_by_session_run_ids_event_types_async(
            session_id=session_id,
            run_ids=(run_id,),
            event_types=_CLASSIFICATION_EVENT_TYPES,
        )

        # 2. Heuristic pre-screening
        tool_call_count = 0
        tool_names: set[str] = set()
        approval_requested = 0
        approval_denied = 0
        guardrail_blocked = False
        spec_checkpoint_applied = 0
        total_tokens = 0

        for evt in events:
            etype = str(evt.get("event_type", ""))
            payload_str = str(evt.get("payload_json", "{}"))
            try:
                payload = json.loads(payload_str)
            except (json.JSONDecodeError, TypeError):
                payload = {}

            if etype == RunEventType.TOOL_CALL.value:
                tool_call_count += 1
                tool_name = payload.get("tool_name", "")
                if tool_name:
                    tool_names.add(tool_name)

            elif etype == RunEventType.TOOL_APPROVAL_REQUESTED.value:
                approval_requested += 1

            elif etype == RunEventType.TOOL_APPROVAL_RESOLVED.value:
                if payload.get("approved") is False or payload.get("resolution") in (
                    "denied",
                    "rejected",
                ):
                    approval_denied += 1

            elif etype == RunEventType.RUNTIME_GUARDRAIL_REPORT.value:
                report_payload = payload.get("payload", payload)
                if isinstance(report_payload, dict):
                    status = report_payload.get("status", "")
                    blocked_count = report_payload.get("blocked_count", 0)
                    if status == "blocked" or (
                        isinstance(blocked_count, int) and blocked_count > 0
                    ):
                        guardrail_blocked = True

            elif etype == RunEventType.SPEC_CHECKPOINT_APPLIED.value:
                spec_checkpoint_applied += 1

            elif etype == RunEventType.TOKEN_USAGE.value:
                tokens = payload.get("total_tokens", 0)
                if isinstance(tokens, (int, float)):
                    total_tokens += int(tokens)

            event_id = str(evt.get("id", ""))
            if event_id:
                evidence_refs.append(event_id)

        # -- Permission friction detection --
        if approval_requested >= 10:
            deny_rate = approval_denied / approval_requested
            if deny_rate > self._approval_deny_rate_threshold:
                detected_modes[FailureMode.PERMISSION_FRICTION] = 0.8
                evidence_parts.append(
                    f"Permission friction: {approval_denied}/{approval_requested} "
                    f"approvals denied ({deny_rate:.0%})"
                )

        if guardrail_blocked:
            if FailureMode.PERMISSION_FRICTION not in detected_modes:
                detected_modes[FailureMode.PERMISSION_FRICTION] = 0.7
            else:
                detected_modes[FailureMode.PERMISSION_FRICTION] = max(
                    detected_modes[FailureMode.PERMISSION_FRICTION], 0.7
                )
            evidence_parts.append("Permission friction: guardrail blocked")

        # -- Tool sprawl detection --
        if (
            tool_call_count >= self._tool_count_threshold
            and len(tool_names) >= self._unique_tool_threshold
        ):
            detected_modes[FailureMode.TOOL_SPRAWL] = 0.8
            evidence_parts.append(
                f"Tool sprawl: {tool_call_count} calls across "
                f"{len(tool_names)} unique tools"
            )
        elif tool_call_count >= self._tool_count_threshold:
            detected_modes[FailureMode.TOOL_SPRAWL] = 0.6
            evidence_parts.append(f"Tool sprawl: {tool_call_count} tool calls")

        # -- Context rot detection --
        context_rot_signals = 0
        if total_tokens > self._token_usage_threshold:
            context_rot_signals += 1
            evidence_parts.append(f"Context rot: {total_tokens} tokens used")
        if spec_checkpoint_applied >= 2:
            context_rot_signals += 1
            evidence_parts.append(
                f"Context rot: {spec_checkpoint_applied} spec checkpoints applied"
            )
        if context_rot_signals >= 2:
            detected_modes[FailureMode.CONTEXT_ROT] = 0.8
        elif context_rot_signals == 1:
            detected_modes[FailureMode.CONTEXT_ROT] = 0.6

        # -- Spec drift detection (delegate to existing evaluator) --
        spec_drift_detected = False
        if task_spec is not None:
            try:
                from relay_teams.agents.execution.spec_drift_evaluator import (
                    evaluate_spec_drift,
                )

                drift_result = await evaluate_spec_drift(
                    spec=task_spec,
                    task_id=f"classify-{run_id}",
                    artifact_id=f"artifact-classify-{run_id}",
                    session_id=session_id,
                    trace_id=run_id,
                    checkpoint_seq=0,
                    evaluator=self._llm_evaluator,
                )
                if drift_result.drift_detected:
                    spec_drift_detected = True
                    detected_modes[FailureMode.SPEC_DRIFT] = 0.85
                    evidence_parts.append(
                        f"Spec drift detected: {drift_result.drift_detail}"
                    )
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="failure_classifier.spec_drift_failed",
                    message="Spec drift evaluation failed",
                    payload={"error": str(exc), "run_id": run_id},
                )

        if (
            verification_report is not None
            and verification_report.passed is False
            and not spec_drift_detected
        ):
            detected_modes[FailureMode.SPEC_DRIFT] = 0.7
            evidence_parts.append("Spec drift: verification report indicates failure")

        # -- Verification miss detection --
        if verification_report is not None and verification_report.passed is True:
            other_failures = any(
                mode
                in (
                    FailureMode.SPEC_DRIFT,
                    FailureMode.TOOL_SPRAWL,
                    FailureMode.PERMISSION_FRICTION,
                )
                for mode in detected_modes
            )
            if other_failures:
                detected_modes[FailureMode.VERIFICATION_MISS] = 0.75
                evidence_parts.append(
                    "Verification miss: report passed but failure signals detected"
                )
            elif guardrail_blocked:
                detected_modes[FailureMode.VERIFICATION_MISS] = 0.7
                evidence_parts.append(
                    "Verification miss: report passed but guardrail blocked"
                )

        # 3. LLM classification for ambiguous cases
        if not detected_modes:
            try:
                llm_result = await self._classify_with_llm(
                    _run_id=run_id,
                    _session_id=session_id,
                    events=events,
                )
                if llm_result:
                    detected_modes.update(llm_result)
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="failure_classifier.llm_failed",
                    message="LLM classification failed, falling back to heuristic",
                    payload={"error": str(exc), "run_id": run_id},
                )

        # 4. Assemble classification
        if not detected_modes:
            # Default: classify as context_rot with low confidence
            primary_mode = FailureMode.CONTEXT_ROT
            primary_confidence = 0.3
            secondary_modes: tuple[FailureMode, ...] = ()
            evidence_parts.append(
                "No strong failure signals detected; "
                "defaulting to context_rot with low confidence"
            )
        else:
            sorted_modes = sorted(
                detected_modes.items(), key=lambda x: x[1], reverse=True
            )
            primary_mode, primary_confidence = sorted_modes[0]
            secondary_modes = tuple(mode for mode, _conf in sorted_modes[1:])

        evidence_summary = (
            "; ".join(evidence_parts)
            if evidence_parts
            else "No failure mode evidence detected"
        )

        classification_id = f"fmc-{uuid4().hex[:12]}"

        return FailureModeClassification(
            classification_id=classification_id,
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            role_id=role_id,
            primary_mode=primary_mode,
            secondary_modes=secondary_modes,
            confidence_score=round(primary_confidence, 2),
            evidence_summary=evidence_summary,
            evidence_refs=tuple(evidence_refs),
            classified_at=datetime.now(timezone.utc),
            classifier_version=self._classifier_version,
        )

    async def classify_batch(
        self,
        *,
        sampled_runs: tuple[SampledRun, ...],
    ) -> ClassificationBatchResult:
        batch_id = f"fcb-{uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc)
        classifications: list[FailureModeClassification] = []
        errors: list[str] = []
        classified_count = 0
        skipped_count = 0

        for run in sampled_runs:
            try:
                classification = await self.classify_run(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    workspace_id=run.workspace_id,
                    role_id=run.role_id,
                    task_spec=None,
                    verification_report=None,
                )
                classifications.append(classification)
                classified_count += 1
            except Exception as exc:
                errors.append(f"run_id={run.run_id}: {type(exc).__name__}: {exc}")
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    event="failure_classifier.batch_error",
                    message=f"Classification failed for run {run.run_id}",
                    payload={"error": str(exc), "run_id": run.run_id},
                )

        completed_at = datetime.now(timezone.utc)

        return ClassificationBatchResult(
            batch_id=batch_id,
            classifications=tuple(classifications),
            errors=tuple(errors),
            started_at=started_at,
            completed_at=completed_at,
            total_runs=len(sampled_runs),
            classified_count=classified_count,
            skipped_count=skipped_count,
        )

    async def _classify_with_llm(
        self,
        *,
        _run_id: str,
        _session_id: str,
        events: Sequence[Mapping[str, object]],
    ) -> dict[FailureMode, float] | None:
        """Attempt LLM-based classification. Returns None on failure."""
        # Build a structured prompt from event summary data
        event_summary_lines: list[str] = []
        tool_calls = 0
        tool_names_set: set[str] = set()
        approvals = 0
        for evt in events[: self._tool_count_threshold]:
            etype = str(evt.get("event_type", ""))
            payload_str = str(evt.get("payload_json", "{}"))
            try:
                payload = json.loads(payload_str)
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if etype == RunEventType.TOOL_CALL.value:
                tool_calls += 1
                tn = payload.get("tool_name", "")
                if tn:
                    tool_names_set.add(tn)
            elif etype in (
                RunEventType.TOOL_APPROVAL_REQUESTED.value,
                RunEventType.TOOL_APPROVAL_RESOLVED.value,
            ):
                approvals += 1

        event_summary_lines.append(f"Total tool calls: {tool_calls}")
        event_summary_lines.append(f"Unique tools: {len(tool_names_set)}")
        event_summary_lines.append(f"Approval events: {approvals}")
        event_summary_lines.append(f"Total events: {len(events)}")

        prompt = (
            "Classify this agent run into one or more failure modes.\n\n"
            "Failure modes:\n"
            "- context_rot: agent lost intent, context overflow\n"
            "- tool_sprawl: too many tool calls or wrong tool choices\n"
            "- spec_drift: output diverged from spec\n"
            "- permission_friction: permission/approval issues\n"
            "- verification_miss: verification passed but defects exist\n\n"
            "Run event summary:\n"
            + "\n".join(f"- {line}" for line in event_summary_lines)
            + "\n\n"
            "Respond with JSON: "
            '{"primary_mode": "<mode>", '
            '"secondary_modes": ["<mode>", ...], '
            '"confidence": 0.0-1.0, '
            '"evidence": "..."}'
        )

        try:
            result = await self._llm_evaluator.run_custom_evaluation(prompt)
            # Try to parse the summary as JSON for mode extraction
            summary = result.summary
            try:
                data = json.loads(summary)
            except json.JSONDecodeError:
                # Try to extract JSON from the response text
                match = re.search(r"\{[^{}]*}", summary)
                if match:
                    try:
                        data = json.loads(match.group())
                    except json.JSONDecodeError:
                        return None
                else:
                    return None

            modes: dict[FailureMode, float] = {}
            primary = data.get("primary_mode", "")
            confidence = float(data.get("confidence", 0.5))
            try:
                primary_mode = FailureMode(primary)
                modes[primary_mode] = min(max(confidence, 0.3), 0.7)
            except ValueError:
                return None

            for sec in data.get("secondary_modes", []):
                try:
                    sec_mode = FailureMode(sec)
                    if sec_mode not in modes:
                        modes[sec_mode] = 0.4
                except ValueError:
                    continue

            return modes

        except (ValueError, KeyError, TypeError, OSError):
            return None
