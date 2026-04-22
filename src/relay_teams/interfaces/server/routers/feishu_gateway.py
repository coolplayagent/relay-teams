# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException

from relay_teams.gateway.feishu.errors import FeishuAccountNameConflictError
from relay_teams.gateway.feishu.gateway_service import FeishuGatewayService
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountCreateInput,
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountUpdateInput,
)
from relay_teams.gateway.feishu.subscription_service import FeishuSubscriptionService
from relay_teams.interfaces.server.deps import (
    get_feishu_gateway_service,
    get_feishu_subscription_service,
)
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.validation import RequiredIdentifierStr

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
    except (FeishuAccountNameConflictError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((FeishuAccountNameConflictError, 409), (ValueError, 422)),
        ) from exc


@router.patch("/accounts/{account_id}", response_model=FeishuGatewayAccountRecord)
def update_feishu_account(
    account_id: RequiredIdentifierStr,
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
    except (KeyError, FeishuAccountNameConflictError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((FeishuAccountNameConflictError, 409), (ValueError, 422)),
        ) from exc


@router.post("/accounts/{account_id}:enable", response_model=FeishuGatewayAccountRecord)
def enable_feishu_account(
    account_id: RequiredIdentifierStr,
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
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/accounts/{account_id}:disable", response_model=FeishuGatewayAccountRecord
)
def disable_feishu_account(
    account_id: RequiredIdentifierStr,
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
    account_id: RequiredIdentifierStr,
    service: Annotated[FeishuGatewayService, Depends(get_feishu_gateway_service)],
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
    req: DeleteRequest | None = Body(default=None),
) -> dict[str, str]:
    try:
        service.delete_account(
            account_id, force=req.force if req is not None else False
        )
        subscription_service.reload()
        return {"status": "ok"}
    except (KeyError, RuntimeError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 409),),
        ) from exc


@router.post("/reload")
def reload_feishu_gateway(
    subscription_service: Annotated[
        FeishuSubscriptionService,
        Depends(get_feishu_subscription_service),
    ],
) -> dict[str, str]:
    subscription_service.reload()
    return {"status": "ok"}
