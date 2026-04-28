from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.reminders.models import (
    CompletionAttemptObservation,
    ContextPressureObservation,
    ReminderDecision,
    ReminderKind,
    ToolResultObservation,
)
from relay_teams.reminders.state import ReminderRunState, can_issue
from relay_teams.reminders.tool_effects import classify_tool_effect
from relay_teams.reminders.models import ToolEffect
from relay_teams.system_reminder_delivery import SystemReminderDeliveryMode


class ReminderPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_failure_cooldown_seconds: int = Field(default=60, ge=0)
    read_only_streak_threshold: int = Field(default=50, ge=1)
    read_only_streak_cooldown_seconds: int = Field(default=600, ge=0)
    context_pressure_cooldown_seconds: int = Field(default=900, ge=0)
    completion_max_retries: int = Field(default=3, ge=0)


class SystemReminderPolicy:
    def __init__(self, config: ReminderPolicyConfig | None = None) -> None:
        self._config = config or ReminderPolicyConfig()

    @property
    def config(self) -> ReminderPolicyConfig:
        return self._config

    def evaluate_tool_result(
        self,
        *,
        observation: ToolResultObservation,
        state: ReminderRunState,
    ) -> tuple[ReminderDecision, ReminderRunState]:
        if not observation.ok:
            next_state = state.model_copy(update={"read_only_streak": 0})
            error_type = observation.error_type or "tool_error"
            issue_key = f"tool_failure:{observation.tool_name}:{error_type}"
            if not can_issue(
                state=next_state,
                issue_key=issue_key,
                cooldown_seconds=self._config.tool_failure_cooldown_seconds,
            ):
                return ReminderDecision(), next_state
            message = observation.error_message or "The tool returned an error."
            return (
                ReminderDecision(
                    issue=True,
                    kind=ReminderKind.TOOL_FAILURE,
                    delivery_mode=SystemReminderDeliveryMode.GUIDANCE,
                    issue_key=issue_key,
                    content=(
                        f"The `{observation.tool_name}` tool failed with `{error_type}`: "
                        f"{message}\n\nInspect the failure, adjust the approach, and do "
                        "not repeat the same failing call unchanged."
                    ),
                    reason=error_type,
                ),
                next_state,
            )

        effect = classify_tool_effect(observation.tool_name)
        if effect == ToolEffect.READ_ONLY:
            next_streak = state.read_only_streak + 1
        else:
            next_streak = 0
        next_state = state.model_copy(update={"read_only_streak": next_streak})
        if effect != ToolEffect.READ_ONLY:
            return ReminderDecision(), next_state
        if next_streak < self._config.read_only_streak_threshold:
            return ReminderDecision(), next_state
        issue_key = "read_only_streak"
        if not can_issue(
            state=next_state,
            issue_key=issue_key,
            cooldown_seconds=self._config.read_only_streak_cooldown_seconds,
        ):
            return ReminderDecision(), next_state
        return (
            ReminderDecision(
                issue=True,
                kind=ReminderKind.READ_ONLY_STREAK,
                delivery_mode=SystemReminderDeliveryMode.GUIDANCE,
                issue_key=issue_key,
                content=(
                    f"You have used {next_streak} read-only tools in a row. If you "
                    "have enough evidence, move toward a concrete change or final "
                    "answer. If more inspection is required, state the specific "
                    "missing fact before continuing."
                ),
                reason="read_only_streak",
            ),
            next_state,
        )

    def evaluate_completion_attempt(
        self,
        *,
        observation: CompletionAttemptObservation,
        state: ReminderRunState,
    ) -> tuple[ReminderDecision, ReminderRunState]:
        if not observation.incomplete_todos:
            return (
                ReminderDecision(),
                state.model_copy(update={"completion_retry_count": 0}),
            )
        retry_count = state.completion_retry_count + 1
        next_state = state.model_copy(update={"completion_retry_count": retry_count})
        formatted_todos = "\n".join(
            f"- [{item.status}] {item.content}" for item in observation.incomplete_todos
        )
        content = (
            "You attempted to finish while run-scoped todos are still incomplete.\n\n"
            f"{formatted_todos}\n\n"
            "Before completing, either finish the pending work or update the todo list "
            "to accurately reflect what remains."
        )
        if retry_count > self._config.completion_max_retries:
            return (
                ReminderDecision(
                    issue=True,
                    kind=ReminderKind.INCOMPLETE_TODOS,
                    delivery_mode=SystemReminderDeliveryMode.COMPLETION_GUARD,
                    issue_key="incomplete_todos:failed_completion",
                    content=content,
                    fail_completion=True,
                    reason="completion_retry_limit_exceeded",
                ),
                next_state,
            )
        return (
            ReminderDecision(
                issue=True,
                kind=ReminderKind.INCOMPLETE_TODOS,
                delivery_mode=SystemReminderDeliveryMode.COMPLETION_GUARD,
                issue_key=f"incomplete_todos:retry:{retry_count}",
                content=content,
                retry_completion=True,
                reason="incomplete_todos",
            ),
            next_state,
        )

    def evaluate_context_pressure(
        self,
        *,
        observation: ContextPressureObservation,
        state: ReminderRunState,
    ) -> tuple[ReminderDecision, ReminderRunState]:
        issue_key = observation.kind.value
        if not can_issue(
            state=state,
            issue_key=issue_key,
            cooldown_seconds=self._config.context_pressure_cooldown_seconds,
        ):
            return ReminderDecision(), state
        if observation.kind == ReminderKind.POST_COMPACTION:
            content = (
                "Conversation history was compacted to preserve the context window. "
                "Prefer using the current transcript and compacted summary rather than "
                "assuming every prior tool output is still available verbatim."
            )
        else:
            content = (
                "The conversation is approaching the context budget. Preserve important "
                "facts in the active answer or todo state before continuing with more "
                "large outputs."
            )
        return (
            ReminderDecision(
                issue=True,
                kind=observation.kind,
                delivery_mode=SystemReminderDeliveryMode.GUIDANCE,
                issue_key=issue_key,
                content=content,
                reason=observation.kind.value,
            ),
            state,
        )
