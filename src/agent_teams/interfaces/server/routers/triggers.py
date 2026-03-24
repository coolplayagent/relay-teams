from __future__ import annotations

import logging
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from agent_teams.interfaces.server.deps import (
    get_feishu_subscription_service,
    get_feishu_trigger_config_service,
    get_trigger_service,
)
from agent_teams.feishu import FeishuSubscriptionService, FeishuTriggerConfigService
from agent_teams.logger import get_logger, log_event
from agent_teams.trace import bind_trace_context
from agent_teams.triggers import (
    TriggerService,
    TriggerAuthRejectedError,
    TriggerCreateInput,
    TriggerDefinition,
    TriggerEventRecord,
    TriggerIngestInput,
    TriggerIngestResult,
    TriggerNameConflictError,
    TriggerSourceType,
    TriggerStatus,
    TriggerUpdateInput,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/triggers", tags=["Triggers"])


class TriggerEventListResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    items: list[TriggerEventRecord]
    next_cursor: str | None = None


@router.post("", response_model=TriggerDefinition)
def create_trigger(
    req: TriggerCreateInput,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
    feishu_subscription_service: Annotated[
        FeishuSubscriptionService, Depends(get_feishu_subscription_service)
    ],
) -> TriggerDefinition:
    try:
        feishu_config_service.validate_create_request(req)
        created = service.create_trigger(req)
        try:
            if _is_feishu_im_trigger(created):
                feishu_config_service.save_secret_config(
                    trigger_id=created.trigger_id,
                    secret_config_payload=req.secret_config,
                    require_app_secret=True,
                )
                feishu_subscription_service.reload()
        except Exception:
            service.delete_trigger(created.trigger_id)
            raise
        with bind_trace_context(trigger_id=created.trigger_id):
            log_event(
                logger,
                logging.INFO,
                event="trigger.created",
                message="Trigger created",
                payload={
                    "name": created.name,
                    "source_type": created.source_type.value,
                },
            )
        return feishu_config_service.attach_secret_status(created)
    except TriggerNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[TriggerDefinition])
def list_triggers(
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
) -> list[TriggerDefinition]:
    return list(feishu_config_service.attach_secret_statuses(service.list_triggers()))


@router.get("/{trigger_id}", response_model=TriggerDefinition)
def get_trigger(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
) -> TriggerDefinition:
    try:
        return feishu_config_service.attach_secret_status(service.get_trigger(trigger_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{trigger_id}", response_model=TriggerDefinition)
def update_trigger(
    trigger_id: str,
    req: TriggerUpdateInput,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
    feishu_subscription_service: Annotated[
        FeishuSubscriptionService, Depends(get_feishu_subscription_service)
    ],
) -> TriggerDefinition:
    try:
        existing = service.get_trigger(trigger_id)
        reload_required = False
        feishu_config_service.validate_update_request(existing=existing, request=req)
        if _is_feishu_im_trigger(existing):
            reload_required = feishu_config_service.subscription_runtime_changed_for_update(
                existing=existing,
                request=req,
            )
        updated = service.update_trigger(trigger_id, req)
        if _is_feishu_im_trigger(updated):
            try:
                feishu_config_service.save_secret_config(
                    trigger_id=updated.trigger_id,
                    secret_config_payload=req.secret_config,
                    require_app_secret=False,
                )
            except Exception:
                _ = service.update_trigger(
                    trigger_id,
                    TriggerUpdateInput(
                        name=existing.name,
                        display_name=existing.display_name,
                        source_config=existing.source_config,
                        auth_policies=existing.auth_policies,
                        target_config=existing.target_config,
                    ),
                )
                raise
            if feishu_config_service.runtime_settings_changed(existing, updated):
                feishu_config_service.clear_bindings(updated.trigger_id)
            if reload_required:
                feishu_subscription_service.reload()
        with bind_trace_context(trigger_id=updated.trigger_id):
            log_event(
                logger,
                logging.INFO,
                event="trigger.updated",
                message="Trigger updated",
                payload={"name": updated.name},
            )
        return feishu_config_service.attach_secret_status(updated)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriggerNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{trigger_id}")
def delete_trigger(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
    feishu_subscription_service: Annotated[
        FeishuSubscriptionService, Depends(get_feishu_subscription_service)
    ],
) -> dict[str, str]:
    try:
        existing = service.get_trigger(trigger_id)
        if _is_feishu_im_trigger(existing):
            feishu_config_service.clear_bindings(trigger_id)
            feishu_config_service.delete_secret_config(trigger_id)
        service.delete_trigger(trigger_id)
        if _is_feishu_im_trigger(existing):
            feishu_subscription_service.reload()
        with bind_trace_context(trigger_id=trigger_id):
            log_event(
                logger,
                logging.INFO,
                event="trigger.deleted",
                message="Trigger deleted",
                payload={
                    "name": existing.name,
                    "source_type": existing.source_type.value,
                },
            )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{trigger_id}:enable", response_model=TriggerDefinition)
def enable_trigger(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
    feishu_subscription_service: Annotated[
        FeishuSubscriptionService, Depends(get_feishu_subscription_service)
    ],
) -> TriggerDefinition:
    try:
        updated = service.set_trigger_status(trigger_id, TriggerStatus.ENABLED)
        if _is_feishu_im_trigger(updated):
            feishu_subscription_service.reload()
        return feishu_config_service.attach_secret_status(updated)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{trigger_id}:disable", response_model=TriggerDefinition)
def disable_trigger(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
    feishu_subscription_service: Annotated[
        FeishuSubscriptionService, Depends(get_feishu_subscription_service)
    ],
) -> TriggerDefinition:
    try:
        updated = service.set_trigger_status(trigger_id, TriggerStatus.DISABLED)
        if _is_feishu_im_trigger(updated):
            feishu_subscription_service.reload()
        return feishu_config_service.attach_secret_status(updated)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{trigger_id}:rotate-token", response_model=TriggerDefinition)
def rotate_trigger_token(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    feishu_config_service: Annotated[
        FeishuTriggerConfigService, Depends(get_feishu_trigger_config_service)
    ],
) -> TriggerDefinition:
    try:
        return feishu_config_service.attach_secret_status(
            service.rotate_public_token(trigger_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ingest", response_model=TriggerIngestResult)
async def ingest_event(
    req: TriggerIngestInput,
    request: Request,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
) -> TriggerIngestResult:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    headers = {name: value for name, value in request.headers.items()}
    remote_addr = request.client.host if request.client is not None else None
    try:
        result = service.ingest_event(
            event=req,
            headers=headers,
            remote_addr=remote_addr,
            raw_body=raw_body,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriggerAuthRejectedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/webhooks/{public_token}", response_model=TriggerIngestResult)
async def ingest_webhook(
    public_token: str,
    request: Request,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
) -> TriggerIngestResult:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    headers = {name: value for name, value in request.headers.items()}
    remote_addr = request.client.host if request.client is not None else None
    try:
        result = service.ingest_webhook(
            public_token=public_token,
            raw_body=raw_body,
            headers=headers,
            remote_addr=remote_addr,
        )
        with bind_trace_context(trigger_id=result.trigger_id):
            log_event(
                logger,
                logging.INFO,
                event="trigger.ingest.accepted",
                message="Webhook event accepted",
                payload={
                    "trigger_name": result.trigger_name,
                    "event_id": result.event_id,
                    "duplicate": result.duplicate,
                },
            )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriggerAuthRejectedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{trigger_id}/events", response_model=TriggerEventListResponse)
def list_trigger_events(
    trigger_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
    limit: int = 50,
    cursor_event_id: str | None = None,
) -> TriggerEventListResponse:
    try:
        items, next_cursor = service.list_events(
            trigger_id,
            limit=limit,
            cursor_event_id=cursor_event_id,
        )
        return TriggerEventListResponse(items=list(items), next_cursor=next_cursor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/events/{event_id}", response_model=TriggerEventRecord)
def get_trigger_event(
    event_id: str,
    service: Annotated[TriggerService, Depends(get_trigger_service)],
) -> TriggerEventRecord:
    try:
        return service.get_event(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _is_feishu_im_trigger(trigger: TriggerDefinition) -> bool:
    provider = str(trigger.source_config.get("provider", "")).strip().lower()
    return trigger.source_type == TriggerSourceType.IM and provider == "feishu"
