from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException

from relay_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatAccountUpdateInput,
    WeChatLoginStartRequest,
    WeChatLoginStartResponse,
    WeChatLoginWaitRequest,
    WeChatLoginWaitResponse,
)
from relay_teams.gateway.wechat.service import WeChatGatewayService
from relay_teams.gateway.xiaoluban import (
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountUpdateInput,
    XiaolubanGatewayService,
)
from relay_teams.interfaces.server.deps import (
    get_wechat_gateway_service,
    get_xiaoluban_gateway_service,
)
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/gateway", tags=["Gateway"])


@router.get("/wechat/accounts", response_model=list[WeChatAccountRecord])
async def list_wechat_accounts(
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> list[WeChatAccountRecord]:
    accounts = await asyncio.to_thread(service.list_accounts)
    return list(accounts)


@router.get("/xiaoluban/accounts", response_model=list[XiaolubanAccountRecord])
async def list_xiaoluban_accounts(
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> list[XiaolubanAccountRecord]:
    accounts = await asyncio.to_thread(service.list_accounts)
    return list(accounts)


@router.post("/xiaoluban/accounts", response_model=XiaolubanAccountRecord)
async def create_xiaoluban_account(
    req: XiaolubanAccountCreateInput,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await asyncio.to_thread(service.create_account, req)
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 422),)) from exc


@router.patch("/xiaoluban/accounts/{account_id}", response_model=XiaolubanAccountRecord)
async def update_xiaoluban_account(
    account_id: RequiredIdentifierStr,
    req: XiaolubanAccountUpdateInput,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await asyncio.to_thread(service.update_account, account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/xiaoluban/accounts/{account_id}:enable",
    response_model=XiaolubanAccountRecord,
)
async def enable_xiaoluban_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await asyncio.to_thread(service.set_account_enabled, account_id, True)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/xiaoluban/accounts/{account_id}:disable",
    response_model=XiaolubanAccountRecord,
)
async def disable_xiaoluban_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await asyncio.to_thread(service.set_account_enabled, account_id, False)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.delete("/xiaoluban/accounts/{account_id}")
async def delete_xiaoluban_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
    req: DeleteRequest | None = Body(default=None),
) -> dict[str, str]:
    try:
        await asyncio.to_thread(
            service.delete_account,
            account_id,
            force=req.force if req is not None else False,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 409),)) from exc


@router.post("/wechat/login/start", response_model=WeChatLoginStartResponse)
async def start_wechat_login(
    req: WeChatLoginStartRequest,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatLoginStartResponse:
    try:
        return await asyncio.to_thread(service.start_login, req)
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 400),)) from exc


@router.post("/wechat/login/wait", response_model=WeChatLoginWaitResponse)
async def wait_wechat_login(
    req: WeChatLoginWaitRequest,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatLoginWaitResponse:
    try:
        return await asyncio.to_thread(service.wait_login, req)
    except (KeyError, RuntimeError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 400),),
        ) from exc


@router.patch("/wechat/accounts/{account_id}", response_model=WeChatAccountRecord)
async def update_wechat_account(
    account_id: RequiredIdentifierStr,
    req: WeChatAccountUpdateInput,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return await asyncio.to_thread(service.update_account, account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post("/wechat/accounts/{account_id}:enable", response_model=WeChatAccountRecord)
async def enable_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return await asyncio.to_thread(service.set_account_enabled, account_id, True)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/wechat/accounts/{account_id}:disable", response_model=WeChatAccountRecord
)
async def disable_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return await asyncio.to_thread(service.set_account_enabled, account_id, False)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.delete("/wechat/accounts/{account_id}")
async def delete_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
    req: DeleteRequest | None = Body(default=None),
) -> dict[str, str]:
    try:
        await asyncio.to_thread(
            service.delete_account,
            account_id,
            force=req.force if req is not None else False,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 409),)) from exc


@router.post("/wechat/reload")
async def reload_wechat_gateway(
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> dict[str, str]:
    await asyncio.to_thread(service.reload)
    return {"status": "ok"}
