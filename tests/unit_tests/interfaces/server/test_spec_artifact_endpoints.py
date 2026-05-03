# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.tasks.models import (
    SpecArtifactDiffResult,
    TaskSpec,
    TaskSpecArtifact,
)
from relay_teams.interfaces.server.routers.tasks import (
    get_spec_artifact_diff,
    list_spec_artifacts,
    list_spec_checkpoint_evaluations,
)


def _make_artifact(task_id: str, version: int) -> TaskSpecArtifact:
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    return TaskSpecArtifact(
        artifact_id=f"art-{version}",
        task_id=task_id,
        session_id="s-1",
        trace_id="t-1",
        spec=TaskSpec(),
        version=version,
        created_at=now,
        updated_at=now,
    )


class TestListSpecArtifactsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_summary_format(self) -> None:
        svc = MagicMock()
        svc.list_task_spec_artifacts_async = AsyncMock(
            return_value=[_make_artifact("t1", 1), _make_artifact("t1", 2)]
        )
        result = await list_spec_artifacts(
            task_id="t1", response_format="summary", service=svc
        )
        assert result["task_id"] == "t1"
        assert len(result["versions"]) == 2

    @pytest.mark.asyncio
    async def test_returns_full_format(self) -> None:
        svc = MagicMock()
        svc.list_task_spec_artifacts_async = AsyncMock(
            return_value=[_make_artifact("t1", 1)]
        )
        result = await list_spec_artifacts(
            task_id="t1", response_format="full", service=svc
        )
        assert result["task_id"] == "t1"
        assert len(result["versions"]) == 1

    @pytest.mark.asyncio
    async def test_task_not_found_raises_404(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        svc.list_task_spec_artifacts_async = AsyncMock(side_effect=KeyError("t1"))
        with pytest.raises(HTTPException) as exc_info:
            await list_spec_artifacts(
                task_id="t1", response_format="summary", service=svc
            )
        assert exc_info.value.status_code == 404


class TestGetSpecArtifactDiffEndpoint:
    @pytest.mark.asyncio
    async def test_version_less_than_1_returns_400(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await get_spec_artifact_diff(
                task_id="t1",
                version=0,
                from_version=None,
                service=svc,
                diff_service=MagicMock(),
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_task_not_found_returns_404(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        svc.get_task_async = AsyncMock(side_effect=KeyError("t1"))
        with pytest.raises(HTTPException) as exc_info:
            await get_spec_artifact_diff(
                task_id="t1",
                version=2,
                from_version=None,
                service=svc,
                diff_service=MagicMock(),
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_version_1_without_from_returns_400(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        svc.get_task_async = AsyncMock(return_value=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await get_spec_artifact_diff(
                task_id="t1",
                version=1,
                from_version=None,
                service=svc,
                diff_service=MagicMock(),
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_successful_diff(self) -> None:
        svc = MagicMock()
        svc.get_task_async = AsyncMock(return_value=MagicMock())
        diff_svc = MagicMock()
        diff_svc.compute_diff_async = AsyncMock(
            return_value=SpecArtifactDiffResult(
                task_id="t1",
                from_artifact_id="art-1",
                to_artifact_id="art-2",
                has_changes=True,
                from_version=1,
                to_version=2,
                field_changes=[],
            )
        )
        result = await get_spec_artifact_diff(
            task_id="t1",
            version=2,
            from_version=1,
            service=svc,
            diff_service=diff_svc,
        )
        assert result["has_changes"] is True

    @pytest.mark.asyncio
    async def test_artifact_not_found_returns_404(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        svc.get_task_async = AsyncMock(return_value=MagicMock())
        diff_svc = MagicMock()
        diff_svc.compute_diff_async = AsyncMock(side_effect=KeyError("artifact"))
        with pytest.raises(HTTPException) as exc_info:
            await get_spec_artifact_diff(
                task_id="t1",
                version=5,
                from_version=1,
                service=svc,
                diff_service=diff_svc,
            )
        assert exc_info.value.status_code == 404


class TestListSpecCheckpointEvaluationsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_evaluations(self) -> None:
        svc = MagicMock()
        svc.list_spec_checkpoint_evaluations_async = AsyncMock(return_value=[])
        result = await list_spec_checkpoint_evaluations(
            task_id="t1", checkpoint_seq=None, service=svc
        )
        assert result["task_id"] == "t1"
        assert result["evaluations"] == []

    @pytest.mark.asyncio
    async def test_task_not_found_returns_404(self) -> None:
        from fastapi import HTTPException

        svc = MagicMock()
        svc.list_spec_checkpoint_evaluations_async = AsyncMock(
            side_effect=KeyError("t1")
        )
        with pytest.raises(HTTPException) as exc_info:
            await list_spec_checkpoint_evaluations(
                task_id="t1", checkpoint_seq=None, service=svc
            )
        assert exc_info.value.status_code == 404
