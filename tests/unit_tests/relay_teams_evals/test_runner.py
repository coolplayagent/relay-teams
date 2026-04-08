from __future__ import annotations

import subprocess
from pathlib import Path

from relay_teams_evals.backends.base import AgentBackend, AgentEvent
from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from relay_teams_evals.runner import EvalRunner
from relay_teams_evals.scorers.base import Scorer
from relay_teams_evals.workspace.base import (
    PreparedWorkspace,
    WorkspaceSetup,
    WorkspaceSetupError,
)
from relay_teams_evals.workspace.patch_extractor import PatchExtractor


class FakeBackend(AgentBackend):
    def run(
        self,
        intent: str,
        workspace: PreparedWorkspace,
        keep_workspace: bool = False,
    ):
        assert intent == "demo"
        assert workspace.container_id == "agent-container"
        yield AgentEvent(type="metadata", run_id="run-1", session_id="session-1")
        yield AgentEvent(
            type="token_usage",
            input_tokens=120,
            cached_input_tokens=30,
            output_tokens=45,
            reasoning_output_tokens=12,
            requests=2,
            tool_calls=1,
        )
        yield AgentEvent(type="text_delta", text="done")
        yield AgentEvent(type="completed")


class FakeWorkspaceSetup(WorkspaceSetup):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cleaned: list[str] = []

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        self.calls.append(f"prepare:{item.item_id}")
        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=Path("."),
            base_commit="abc123",
            container_id="agent-container",
            agent_base_url="http://localhost:8000",
            container_repo_path="/testbed",
        )

    def cleanup(self, workspace: PreparedWorkspace) -> None:
        self.cleaned.append(workspace.container_id or "")


class FakePatchExtractor(PatchExtractor):
    def extract(self, workspace: PreparedWorkspace) -> str:
        assert workspace.container_id == "agent-container"
        return (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/tests/test_fix.py b/tests/test_fix.py\n"
            "--- a/tests/test_fix.py\n"
            "+++ b/tests/test_fix.py\n"
            "@@ -1 +1 @@\n"
            "-old test\n"
            "+new test\n"
        )


class FakeScorer(Scorer):
    def __init__(self) -> None:
        self.received_workspace: PreparedWorkspace | None = None
        self.received_patch = ""
        self.received_raw_patch = ""
        self.received_filtered_files: tuple[str, ...] = ()
        self.received_token_usage = TokenUsage()

    @property
    def name(self) -> str:
        return "swebench_docker"

    def score(
        self,
        *,
        item: EvalItem,
        run_id: str,
        session_id: str,
        outcome: RunOutcome,
        agent_output: str,
        generated_patch: str,
        raw_generated_patch: str,
        filtered_generated_files: tuple[str, ...],
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
        error: str | None = None,
    ) -> EvalResult:
        self.received_workspace = workspace
        self.received_patch = generated_patch
        self.received_raw_patch = raw_generated_patch
        self.received_filtered_files = filtered_generated_files
        self.received_token_usage = token_usage
        return EvalResult(
            item_id=item.item_id,
            dataset=item.dataset,
            run_id=run_id,
            session_id=session_id,
            outcome=outcome,
            passed=True,
            score=1.0,
            scorer_name=self.name,
            generated_patch=generated_patch,
            raw_generated_patch=raw_generated_patch,
            filtered_generated_files=filtered_generated_files,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            error=error,
        )


class FakeArtifactCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, EvalResult, PreparedWorkspace | None]] = []

    def collect(
        self,
        item: EvalItem,
        result: EvalResult,
        workspace: PreparedWorkspace | None,
    ) -> None:
        self.calls.append((item.item_id, result, workspace))


def test_runner_extracts_patch_and_passes_to_scorer() -> None:
    item = EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        test_patch=(
            "diff --git a/tests/test_fix.py b/tests/test_fix.py\n"
            "--- a/tests/test_fix.py\n"
            "+++ b/tests/test_fix.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        fail_to_pass=("tests/test_fix.py::test_fix",),
    )
    backend = FakeBackend()
    scorer = FakeScorer()
    workspace_setup = FakeWorkspaceSetup()

    runner = EvalRunner(
        backend=backend,
        scorer=scorer,
        workspace_setup=workspace_setup,
        patch_extractor=FakePatchExtractor(),
        keep_workspaces=False,
    )

    result = runner.run_item(item)

    assert result.passed is True
    assert workspace_setup.calls == ["prepare:demo"]
    assert workspace_setup.cleaned == ["agent-container"]
    assert scorer.received_workspace is not None
    assert scorer.received_workspace.container_id == "agent-container"
    assert "src/app.py" in scorer.received_patch
    assert "tests/test_fix.py" in scorer.received_patch
    assert scorer.received_patch == scorer.received_raw_patch
    assert scorer.received_token_usage == TokenUsage(
        input_tokens=120,
        cached_input_tokens=30,
        output_tokens=45,
        reasoning_output_tokens=12,
        total_tokens=165,
        total_requests=2,
        total_tool_calls=1,
    )


def test_runner_retries_retryable_prepare_failures_and_returns_success() -> None:
    item = EvalItem(item_id="demo", dataset="swebench", intent="demo")
    backend = FakeBackend()
    scorer = FakeScorer()
    artifact_collector = FakeArtifactCollector()

    class FlakyWorkspaceSetup(FakeWorkspaceSetup):
        def __init__(self) -> None:
            super().__init__()
            self.prepare_attempts = 0

        def prepare(self, item: EvalItem) -> PreparedWorkspace:
            self.prepare_attempts += 1
            if self.prepare_attempts < 3:
                raise subprocess.CalledProcessError(125, ["docker", "run"])
            return super().prepare(item)

    workspace_setup = FlakyWorkspaceSetup()
    runner = EvalRunner(
        backend=backend,
        scorer=scorer,
        workspace_setup=workspace_setup,
        artifact_collector=artifact_collector,
        keep_workspaces=False,
        infra_retry_attempts=2,
        infra_retry_backoff_seconds=0.0,
    )

    result = runner.run_item(item)

    assert result.passed is True
    assert workspace_setup.prepare_attempts == 3
    assert workspace_setup.cleaned == ["agent-container"]
    assert len(artifact_collector.calls) == 1
    assert artifact_collector.calls[0][1].passed is True


def test_runner_returns_failed_result_after_retry_exhausted() -> None:
    item = EvalItem(item_id="demo", dataset="swebench", intent="demo")
    artifact_collector = FakeArtifactCollector()

    class BrokenWorkspaceSetup(FakeWorkspaceSetup):
        def __init__(self) -> None:
            super().__init__()
            self.prepare_attempts = 0

        def prepare(self, item: EvalItem) -> PreparedWorkspace:
            self.prepare_attempts += 1
            raise subprocess.CalledProcessError(125, ["docker", "run"])

    workspace_setup = BrokenWorkspaceSetup()
    runner = EvalRunner(
        backend=FakeBackend(),
        scorer=FakeScorer(),
        workspace_setup=workspace_setup,
        artifact_collector=artifact_collector,
        keep_workspaces=False,
        infra_retry_attempts=1,
        infra_retry_backoff_seconds=0.0,
    )

    result = runner.run_item(item)

    assert result.passed is False
    assert result.scorer_detail == "exception during run"
    assert workspace_setup.prepare_attempts == 2
    assert len(artifact_collector.calls) == 1
    assert artifact_collector.calls[0][1].build_log_path is None


def test_runner_does_not_retry_non_retryable_workspace_build_failures() -> None:
    item = EvalItem(item_id="demo", dataset="swebench", intent="demo")
    artifact_collector = FakeArtifactCollector()

    class BrokenWorkspaceSetup(FakeWorkspaceSetup):
        def __init__(self) -> None:
            super().__init__()
            self.prepare_attempts = 0

        def prepare(self, item: EvalItem) -> PreparedWorkspace:
            _ = item
            self.prepare_attempts += 1
            raise WorkspaceSetupError(
                "Instance image 'sweb.eval.x86_64.demo:latest' failed to build.",
                retryable=False,
                build_log_path="logs/build_images/demo/build_image.log",
                build_error_summary="ModuleNotFoundError: No module named 'pkg_resources'",
            )

    workspace_setup = BrokenWorkspaceSetup()
    runner = EvalRunner(
        backend=FakeBackend(),
        scorer=FakeScorer(),
        workspace_setup=workspace_setup,
        artifact_collector=artifact_collector,
        keep_workspaces=False,
        infra_retry_attempts=2,
        infra_retry_backoff_seconds=0.0,
    )

    result = runner.run_item(item)

    assert result.passed is False
    assert result.scorer_detail == "instance image build failed"
    assert result.build_log_path == "logs/build_images/demo/build_image.log"
    assert result.build_error_summary is not None
    assert "pkg_resources" in result.build_error_summary
    assert workspace_setup.prepare_attempts == 1
    assert len(artifact_collector.calls) == 1
