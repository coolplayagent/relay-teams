# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.interfaces.server.deps import get_container
from relay_teams.interfaces.server.container import ServerContainer
from relay_teams.tools.generated_tools import (
    GeneratedToolRecord,
    GeneratedToolStatus,
)

router = APIRouter(prefix="/auto-harness", tags=["AutoHarness"])


class GeneratedToolSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    description: str
    status: GeneratedToolStatus
    target_role_id: str
    created_by_role_id: str
    version: int = Field(default=1, ge=1)
    test_count: int = Field(default=0, ge=0)


def _record_to_summary(record: GeneratedToolRecord) -> GeneratedToolSummary:
    return GeneratedToolSummary(
        tool_name=record.tool_name,
        description=record.description,
        status=record.status,
        target_role_id=record.target_role_id,
        created_by_role_id=record.created_by_role_id,
        version=record.version,
        test_count=len(record.test_cases),
    )


class GeneratedToolDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    description: str
    input_schema: dict[str, JsonValue]
    test_cases: tuple[dict[str, JsonValue], ...]
    status: GeneratedToolStatus
    target_role_id: str
    created_by_role_id: str
    version: int = Field(default=1, ge=1)


def _record_to_detail(record: GeneratedToolRecord) -> GeneratedToolDetail:
    return GeneratedToolDetail(
        tool_name=record.tool_name,
        description=record.description,
        input_schema=record.input_schema,
        test_cases=tuple(case.model_dump(mode="json") for case in record.test_cases),
        status=record.status,
        target_role_id=record.target_role_id,
        created_by_role_id=record.created_by_role_id,
        version=record.version,
    )


def _get_auto_harness_service(request: object) -> object:
    container: ServerContainer = get_container(request)  # type: ignore[arg-type]
    return container.auto_harness_service


@router.get("/tools", response_model=list[GeneratedToolSummary])
async def list_generated_tools(
    request: object,
) -> list[GeneratedToolSummary]:
    from relay_teams.tools.generated_tools import AutoHarnessService

    service: AutoHarnessService = _get_auto_harness_service(request)  # type: ignore[assignment]
    records = service.list_records()
    return [_record_to_summary(record) for record in records]


@router.get("/tools/{tool_name}", response_model=GeneratedToolDetail)
async def get_generated_tool(
    tool_name: str,
    request: object,
) -> GeneratedToolDetail:
    from relay_teams.tools.generated_tools import AutoHarnessService

    service: AutoHarnessService = _get_auto_harness_service(request)  # type: ignore[assignment]
    try:
        record = service._load_record(tool_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    return _record_to_detail(record)
