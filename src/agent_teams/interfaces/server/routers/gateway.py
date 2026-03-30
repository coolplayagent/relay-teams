from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from agent_teams.interfaces.server.deps import get_wechat_gateway_service
from agent_teams.gateway.wechat import (
    WeChatAccountRecord,
    WeChatAccountUpdateInput,
    WeChatGatewayService,
    WeChatLoginStartRequest,
    WeChatLoginStartResponse,
    WeChatLoginWaitRequest,
    WeChatLoginWaitResponse,
)
from agent_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/gateway", tags=["Gateway"])


@router.get("/wechat/accounts", response_model=list[WeChatAccountRecord])
def list_wechat_accounts(
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> list[WeChatAccountRecord]:
    return list(service.list_accounts())


@router.post("/wechat/login/start", response_model=WeChatLoginStartResponse)
def start_wechat_login(
    req: WeChatLoginStartRequest,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatLoginStartResponse:
    try:
        return service.start_login(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wechat/login/wait", response_model=WeChatLoginWaitResponse)
def wait_wechat_login(
    req: WeChatLoginWaitRequest,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatLoginWaitResponse:
    try:
        return service.wait_login(req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/wechat/accounts/{account_id}", response_model=WeChatAccountRecord)
def update_wechat_account(
    account_id: RequiredIdentifierStr,
    req: WeChatAccountUpdateInput,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return service.update_account(account_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/wechat/accounts/{account_id}:enable", response_model=WeChatAccountRecord)
def enable_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return service.set_account_enabled(account_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/wechat/accounts/{account_id}:disable", response_model=WeChatAccountRecord
)
def disable_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return service.set_account_enabled(account_id, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/wechat/accounts/{account_id}")
def delete_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> dict[str, str]:
    try:
        service.delete_account(account_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/wechat/reload")
def reload_wechat_gateway(
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> dict[str, str]:
    service.reload()
    return {"status": "ok"}
