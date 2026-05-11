from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.binary_tools import (
    BinaryToolDownloadJob,
    BinaryToolDownloadStatus,
    BinaryToolId,
    BinaryToolItem,
    BinaryToolListResponse,
    BinaryToolPathSource,
    BinaryToolSourceKind,
    BinaryToolStatus,
    UnsupportedBinaryToolError,
)
from relay_teams.connector import (
    ConnectorAuthType,
    ConnectorCategory,
    ConnectorHealthCheck,
    ConnectorItem,
    ConnectorListResponse,
    ConnectorProvider,
    ConnectorStatus,
    ConnectorSummary,
    ConnectorTestResult,
)
from relay_teams.interfaces.server.deps import (
    get_binary_tool_service,
    get_connector_service,
)
from relay_teams.interfaces.server.routers import connectors


class _FakeConnectorService:
    async def list_connectors(self) -> ConnectorListResponse:
        return ConnectorListResponse(
            summary=ConnectorSummary(
                connected=1,
                needs_config=0,
                disabled=0,
                error=0,
                total=1,
            ),
            items=(
                ConnectorItem(
                    connector_id="github",
                    provider=ConnectorProvider.GITHUB,
                    category=ConnectorCategory.DEVELOPMENT,
                    display_name="GitHub",
                    description="Connect GitHub repositories.",
                    status=ConnectorStatus.CONNECTED,
                    auth_type=ConnectorAuthType.API_TOKEN,
                    account_count=1,
                    enabled_count=1,
                    last_activity_at=_now(),
                    capabilities=("repositories",),
                ),
            ),
        )

    async def test_connector(self, connector_id: str) -> ConnectorTestResult:
        if connector_id != "github":
            raise KeyError(f"Unknown connector_id: {connector_id}")
        return ConnectorTestResult(
            connector_id="github",
            provider=ConnectorProvider.GITHUB,
            status=ConnectorStatus.CONNECTED,
            ok=True,
            checked_at=_now(),
            message="GitHub connection is healthy.",
            account_count=1,
            enabled_count=1,
            capabilities=("repositories",),
            checks=(
                ConnectorHealthCheck(
                    name="github_connectivity",
                    ok=True,
                    message="GitHub probe completed.",
                ),
            ),
        )


class _FakeBinaryToolService:
    def __init__(self) -> None:
        self.job = BinaryToolDownloadJob(
            job_id="bin_test",
            tool_id=BinaryToolId.RIPGREP,
            status=BinaryToolDownloadStatus.RUNNING,
            progress_percent=25,
            message="Downloading archive.",
        )

    async def list_tools(self) -> BinaryToolListResponse:
        return BinaryToolListResponse(
            items=(
                BinaryToolItem(
                    tool_id=BinaryToolId.RIPGREP,
                    display_name="ripgrep",
                    version="14.1.1",
                    source_kind=BinaryToolSourceKind.GITHUB_RELEASE,
                    status=BinaryToolStatus.READY,
                    path_source=BinaryToolPathSource.MANAGED,
                    path="/tmp/rg",
                    executable_name="rg",
                ),
            )
        )

    async def start_download(self, tool_id: str) -> BinaryToolDownloadJob:
        if tool_id != "rg":
            raise UnsupportedBinaryToolError(tool_id)
        return self.job

    def get_download_job(self, job_id: str) -> BinaryToolDownloadJob:
        if job_id != self.job.job_id:
            raise KeyError(job_id)
        return self.job


def test_list_connectors_router_returns_summary_and_real_items() -> None:
    client = _client()

    response = client.get("/api/connectors")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["connected"] == 1
    assert [item["provider"] for item in payload["items"]] == ["github"]
    assert "gmail" not in response.text
    assert "slack" not in response.text
    assert "jira" not in response.text


def test_test_connector_router_returns_probe_result() -> None:
    client = _client()

    response = client.post("/api/connectors/github:test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connector_id"] == "github"
    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "github_connectivity"


def test_test_connector_router_returns_404_for_unknown_connector() -> None:
    client = _client()

    response = client.post("/api/connectors/gmail:test")

    assert response.status_code == 404


def test_list_runtime_tools_router_returns_items() -> None:
    client = _client()

    response = client.get("/api/connectors/runtime-tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["tool_id"] == "rg"
    assert payload["items"][0]["status"] == "ready"


def test_download_runtime_tool_router_returns_job() -> None:
    client = _client()

    response = client.post("/api/connectors/runtime-tools/rg:download")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == "bin_test"
    assert payload["progress_percent"] == 25


def test_download_runtime_tool_router_returns_404_for_unknown_tool() -> None:
    client = _client()

    response = client.post("/api/connectors/runtime-tools/nope:download")

    assert response.status_code == 404


def test_get_runtime_tool_download_router_returns_job() -> None:
    client = _client()

    response = client.get("/api/connectors/runtime-tools/downloads/bin_test")

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(connectors.router, prefix="/api")
    app.dependency_overrides[get_connector_service] = _FakeConnectorService
    app.dependency_overrides[get_binary_tool_service] = _FakeBinaryToolService
    return TestClient(app)


def _now() -> datetime:
    return datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
