# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from agent_teams.gateway.feishu import (
    FeishuAccountNameConflictError,
    FeishuGatewayAccountCreateInput,
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountUpdateInput,
    FeishuGatewayService,
    FeishuSubscriptionService,
)
from agent_teams.interfaces.server.deps import (
    get_feishu_gateway_service,
    get_feishu_subscription_service,
)

router = APIRouter(prefix="/gateway/feishu", tags=["Gateway"])


@router.get("/accounts", response_model=list[FeishuGatewayAccountRecord])
def list_feishu_accounts(
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
) -> list[FeishuGatewayAccountRecord]:
    return list(service.list_accounts())


@router.post("/accounts", response_model=FeishuGatewayAccountRecord)
def create_feishu_account(
    req: FeishuGatewayAccountCreateInput,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> FeishuGatewayAccountRecord:
    try:
        created = service.create_account(req)
        subscription_service.reload()
        return created
    except FeishuAccountNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/accounts/{account_id}", response_model=FeishuGatewayAccountRecord)
def update_feishu_account(
    account_id: str,
    req: FeishuGatewayAccountUpdateInput,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> FeishuGatewayAccountRecord:
    try:
        reload_required = False
        existing = service.get_account(account_id)
        reload_required = service.subscription_runtime_changed_for_update(
            existing=existing,
            request=req,
        )
        updated = service.update_account(account_id, req)
        if reload_required:
            subscription_service.reload()
        return updated
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FeishuAccountNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/accounts/{account_id}:enable", response_model=FeishuGatewayAccountRecord)
def enable_feishu_account(
    account_id: str,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> FeishuGatewayAccountRecord:
    try:
        updated = service.set_account_enabled(account_id, True)
        subscription_service.reload()
        return updated
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/accounts/{account_id}:disable", response_model=FeishuGatewayAccountRecord
)
def disable_feishu_account(
    account_id: str,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> FeishuGatewayAccountRecord:
    try:
        updated = service.set_account_enabled(account_id, False)
        subscription_service.reload()
        return updated
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/accounts/{account_id}")
def delete_feishu_account(
    account_id: str,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> dict[str, str]:
    try:
        service.delete_account(account_id)
        subscription_service.reload()
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reload")
def reload_feishu_gateway(
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> dict[str, str]:
    subscription_service.reload()
    return {"status": "ok"}
