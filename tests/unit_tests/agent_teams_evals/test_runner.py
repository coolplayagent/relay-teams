from __future__ import annotations

from pathlib import Path

from agent_teams_evals.backends.base import AgentBackend, AgentEvent
from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.runner import EvalRunner
from agent_teams_evals.scorers.base import Scorer
from agent_teams_evals.workspace.base import PreparedWorkspace, WorkspaceSetup
from agent_teams_evals.workspace.patch_extractor import PatchExtractor


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

    def prepare_score(self, item: EvalItem) -> PreparedWorkspace:
        self.calls.append(f"prepare_score:{item.item_id}")
        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=Path("."),
            base_commit="abc123",
            container_id="score-container",
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


def test_runner_uses_separate_score_workspace_and_filters_benchmark_test_files() -> None:
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
    assert workspace_setup.calls == ["prepare:demo", "prepare_score:demo"]
    assert workspace_setup.cleaned == ["score-container", "agent-container"]
    assert scorer.received_workspace is not None
    assert scorer.received_workspace.container_id == "score-container"
    assert "src/app.py" in scorer.received_patch
    assert "tests/test_fix.py" not in scorer.received_patch
    assert "tests/test_fix.py" in scorer.received_raw_patch
    assert scorer.received_filtered_files == ("tests/test_fix.py",)
    assert scorer.received_token_usage == TokenUsage(
        input_tokens=120,
        cached_input_tokens=30,
        output_tokens=45,
        reasoning_output_tokens=12,
        total_tokens=165,
        total_requests=2,
        total_tool_calls=1,
    )
