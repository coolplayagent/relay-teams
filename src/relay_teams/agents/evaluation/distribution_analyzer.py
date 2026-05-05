# -*- coding: utf-8 -*-
from __future__ import annotations

from uuid import uuid4

from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)
from relay_teams.agents.evaluation.mvh_report import (
    HarnessPriorityItem,
    MVHRecommendationReport,
)

_HARNESS_LAYER_MAP: dict[FailureMode, str] = {
    FailureMode.CONTEXT_ROT: "context_engineering",
    FailureMode.TOOL_SPRAWL: "tool_policy",
    FailureMode.SPEC_DRIFT: "spec_drift_detection",
    FailureMode.PERMISSION_FRICTION: "permission_gate",
    FailureMode.VERIFICATION_MISS: "verification",
}

_RECOMMENDED_ACTION_MAP: dict[FailureMode, str] = {
    FailureMode.CONTEXT_ROT: (
        "Invest in spec refresh strategies, context window management, "
        "and compaction tuning"
    ),
    FailureMode.TOOL_SPRAWL: (
        "Invest in tool call budgeting, tool selection guidance, "
        "and execution path validation"
    ),
    FailureMode.SPEC_DRIFT: (
        "Invest in drift detection sensitivity, re-alignment protocols, "
        "and checkpoint frequency"
    ),
    FailureMode.PERMISSION_FRICTION: (
        "Invest in permission pre-checking, approval streamlining, "
        "and guardrail policy tuning"
    ),
    FailureMode.VERIFICATION_MISS: (
        "Invest in gate strictness, cross-evaluation, and evidence cross-referencing"
    ),
}

_ALL_FAILURE_MODES: tuple[FailureMode, ...] = (
    FailureMode.CONTEXT_ROT,
    FailureMode.TOOL_SPRAWL,
    FailureMode.SPEC_DRIFT,
    FailureMode.PERMISSION_FRICTION,
    FailureMode.VERIFICATION_MISS,
)


class DistributionAnalyzer:
    """Stateless analyzer that computes failure mode distributions."""

    def __init__(self) -> None:
        pass

    def analyze(
        self,
        *,
        classifications: tuple[FailureModeClassification, ...],
        total_runs_available: int,
    ) -> MVHRecommendationReport:
        total = len(classifications)

        # 1. Compute failure_distribution: count per primary_mode
        failure_distribution: dict[FailureMode, int] = {
            mode: 0 for mode in _ALL_FAILURE_MODES
        }
        for c in classifications:
            failure_distribution[c.primary_mode] += 1

        # 2. Compute percentages
        if total > 0:
            failure_mode_percentages: dict[FailureMode, float] = {
                mode: round(count / total * 100.0, 1)
                for mode, count in failure_distribution.items()
            }
        else:
            failure_mode_percentages = {mode: 0.0 for mode in _ALL_FAILURE_MODES}

        # 3. Multi-mode rate
        if total > 0:
            multi_mode_count = sum(
                1 for c in classifications if len(c.secondary_modes) > 0
            )
            multi_mode_rate = round(multi_mode_count / total, 2)
        else:
            multi_mode_rate = 0.0

        # 4. Build harness_layer_priorities
        priority_items: list[HarnessPriorityItem] = []
        # Sort modes by prevalence descending
        sorted_modes = sorted(
            _ALL_FAILURE_MODES,
            key=lambda m: failure_distribution[m],
            reverse=True,
        )
        for rank_idx, mode in enumerate(sorted_modes, start=1):
            prevalence = failure_mode_percentages[mode]
            if prevalence > 0.0 or rank_idx <= len(_ALL_FAILURE_MODES):
                priority_items.append(
                    HarnessPriorityItem(
                        rank=rank_idx,
                        harness_layer=_HARNESS_LAYER_MAP[mode],
                        failure_mode=mode,
                        prevalence_pct=prevalence,
                        recommended_action=_RECOMMENDED_ACTION_MAP[mode],
                    )
                )

        # 5. Generate summary narrative
        summary = self._build_summary(
            classifications=classifications,
            failure_distribution=failure_distribution,
            multi_mode_rate=multi_mode_rate,
            priority_items=priority_items,
        )

        # 6. Assign report_id
        report_id = f"mvh-{uuid4().hex[:12]}"

        return MVHRecommendationReport(
            report_id=report_id,
            sample_size=total if total > 0 else 1,
            total_runs_available=max(total_runs_available, total),
            failure_distribution=failure_distribution,
            failure_mode_percentages=failure_mode_percentages,
            multi_mode_rate=multi_mode_rate,
            harness_layer_priorities=tuple(priority_items),
            summary=summary,
            classifications=classifications,
        )

    @staticmethod
    def _build_summary(
        *,
        classifications: tuple[FailureModeClassification, ...],
        failure_distribution: dict[FailureMode, int],
        multi_mode_rate: float,
        priority_items: list[HarnessPriorityItem],
    ) -> str:
        if not classifications:
            return "No runs classified. No failure mode data available."

        # Find dominant mode
        dominant_mode = max(failure_distribution, key=lambda m: failure_distribution[m])
        dominant_count = failure_distribution[dominant_mode]
        total = len(classifications)

        dominant_pct = round(dominant_count / total * 100.0, 1) if total > 0 else 0.0

        mode_labels: dict[FailureMode, str] = {
            FailureMode.CONTEXT_ROT: "context rot",
            FailureMode.TOOL_SPRAWL: "tool sprawl",
            FailureMode.SPEC_DRIFT: "spec drift",
            FailureMode.PERMISSION_FRICTION: "permission friction",
            FailureMode.VERIFICATION_MISS: "verification miss",
        }

        parts: list[str] = [
            f"Dominant failure mode is {mode_labels[dominant_mode]} "
            f"({dominant_count}/{total} runs, {dominant_pct}%)."
        ]

        if multi_mode_rate > 0.2:
            parts.append(
                f"{multi_mode_rate:.0%} of runs exhibited multiple failure modes."
            )

        if priority_items:
            top = priority_items[0]
            parts.append(f"Top priority: invest in {top.harness_layer} harness layer.")

        return " ".join(parts)
