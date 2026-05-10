from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Body, Depends, HTTPException

from relay_teams.gateway.discord import (
    DiscordAccountCreateInput,
    DiscordAccountRecord,
    DiscordAccountUpdateInput,
    DiscordGatewayService,
)
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
    XiaolubanAccountStatus,
    XiaolubanAccountUpdateInput,
    XiaolubanGatewayService,
    XiaolubanImConfigUpdateInput,
    XiaolubanImForwardingCommandResponse,
    XiaolubanImListenerService,
    XiaolubanTokenRevealResponse,
)
from relay_teams.interfaces.server.deps import (
    get_discord_gateway_service,
    get_wechat_gateway_service,
    get_xiaoluban_gateway_service,
    get_xiaoluban_im_listener_service,
)
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/gateway", tags=["Gateway"])


@router.get("/wechat/accounts", response_model=list[WeChatAccountRecord])
async def list_wechat_accounts(
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> list[WeChatAccountRecord]:
    accounts = await service.list_accounts_async()
    return list(accounts)


@router.get("/discord/accounts", response_model=list[DiscordAccountRecord])
async def list_discord_accounts(
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> list[DiscordAccountRecord]:
    accounts = await service.list_accounts()
    return list(accounts)


@router.post("/discord/accounts", response_model=DiscordAccountRecord)
async def create_discord_account(
    req: DiscordAccountCreateInput,
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> DiscordAccountRecord:
    try:
        return await service.create_account(req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 400),)) from exc


@router.get("/xiaoluban/accounts", response_model=list[XiaolubanAccountRecord])
async def list_xiaoluban_accounts(
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> list[XiaolubanAccountRecord]:
    accounts = await service.list_accounts_async()
    return list(accounts)


@router.post("/xiaoluban/accounts", response_model=XiaolubanAccountRecord)
async def create_xiaoluban_account(
    req: XiaolubanAccountCreateInput,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await service.create_account_async(req)
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 422),)) from exc


@router.post(
    "/xiaoluban/accounts:prepare",
    response_model=XiaolubanImForwardingCommandResponse,
)
async def prepare_xiaoluban_account(
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
    listener_service: Annotated[
        XiaolubanImListenerService,
        Depends(get_xiaoluban_im_listener_service),
    ],
) -> XiaolubanImForwardingCommandResponse:
    try:
        account_id = await service.prepare_account_id_async()
        forwarding_url = _xiaoluban_forwarding_url(
            listener_service.callback_url(account_id=account_id)
        )
        return XiaolubanImForwardingCommandResponse(
            account_id=account_id,
            forwarding_url=forwarding_url,
            forwarding_command=f"{forwarding_url} g",
            listener_running=listener_service.is_running(),
        )
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 409),)) from exc


@router.patch("/xiaoluban/accounts/{account_id}", response_model=XiaolubanAccountRecord)
async def update_xiaoluban_account(
    account_id: RequiredIdentifierStr,
    req: XiaolubanAccountUpdateInput,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await service.update_account_async(account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/xiaoluban/accounts/{account_id}:reveal-token",
    response_model=XiaolubanTokenRevealResponse,
)
async def reveal_xiaoluban_account_token(
    account_id: RequiredIdentifierStr,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanTokenRevealResponse:
    try:
        return await service.reveal_token_async(account_id)
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.patch(
    "/xiaoluban/accounts/{account_id}/im",
    response_model=XiaolubanAccountRecord,
)
async def update_xiaoluban_im_config(
    account_id: RequiredIdentifierStr,
    req: XiaolubanImConfigUpdateInput,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
) -> XiaolubanAccountRecord:
    try:
        return await service.update_im_config_async(account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.get(
    "/xiaoluban/accounts/{account_id}/im:forwarding-command",
    response_model=XiaolubanImForwardingCommandResponse,
)
async def get_xiaoluban_im_forwarding_command(
    account_id: RequiredIdentifierStr,
    service: Annotated[XiaolubanGatewayService, Depends(get_xiaoluban_gateway_service)],
    listener_service: Annotated[
        XiaolubanImListenerService,
        Depends(get_xiaoluban_im_listener_service),
    ],
) -> XiaolubanImForwardingCommandResponse:
    try:
        account = await service.get_account_async(account_id)
        if account.status != XiaolubanAccountStatus.ENABLED:
            raise ValueError("xiaoluban_account_disabled")
        if not account.im_config.workspace_id:
            raise ValueError("xiaoluban_im_workspace_missing")
        await service.validate_im_workspace_async(account.im_config.workspace_id)
        _ = await service.get_im_callback_auth_token_async(account_id)
        forwarding_url = _xiaoluban_forwarding_url(
            listener_service.callback_url(account_id=account_id)
        )
        return XiaolubanImForwardingCommandResponse(
            account_id=account_id,
            forwarding_url=forwarding_url,
            forwarding_command=f"{forwarding_url} g",
            listener_running=listener_service.is_running(),
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422), (RuntimeError, 409)),
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
        return await service.set_account_enabled_async(account_id, True)
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
        return await service.set_account_enabled_async(account_id, False)
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
        await service.delete_account_async(
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
        return await service.start_login_async(req)
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 400),)) from exc


@router.post("/wechat/login/wait", response_model=WeChatLoginWaitResponse)
async def wait_wechat_login(
    req: WeChatLoginWaitRequest,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatLoginWaitResponse:
    try:
        return await service.wait_login_async(req)
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
        return await service.update_account_async(account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.patch("/discord/accounts/{account_id}", response_model=DiscordAccountRecord)
async def update_discord_account(
    account_id: RequiredIdentifierStr,
    req: DiscordAccountUpdateInput,
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> DiscordAccountRecord:
    try:
        return await service.update_account(account_id, req)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 400),)) from exc


@router.post("/wechat/accounts/{account_id}:enable", response_model=WeChatAccountRecord)
async def enable_wechat_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[WeChatGatewayService, Depends(get_wechat_gateway_service)],
) -> WeChatAccountRecord:
    try:
        return await service.set_account_enabled_async(account_id, True)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/discord/accounts/{account_id}:enable",
    response_model=DiscordAccountRecord,
)
async def enable_discord_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> DiscordAccountRecord:
    try:
        return await service.set_account_enabled(account_id, True)
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
        return await service.set_account_enabled_async(account_id, False)
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/discord/accounts/{account_id}:disable",
    response_model=DiscordAccountRecord,
)
async def disable_discord_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> DiscordAccountRecord:
    try:
        return await service.set_account_enabled(account_id, False)
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
        await service.delete_account_async(
            account_id,
            force=req.force if req is not None else False,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise http_exception_for(exc, mappings=((RuntimeError, 409),)) from exc


@router.delete("/discord/accounts/{account_id}")
async def delete_discord_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
    req: DeleteRequest | None = Body(default=None),
) -> dict[str, str]:
    try:
        await service.delete_account(
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
    await service.reload_async()
    return {"status": "ok"}


@router.post("/discord/reload")
async def reload_discord_gateway(
    service: Annotated[DiscordGatewayService, Depends(get_discord_gateway_service)],
) -> dict[str, str]:
    await service.reload_async()
    return {"status": "ok"}


def _xiaoluban_forwarding_url(callback_url: str) -> str:
    # Xiaoluban manual forwarding rejects query-string callback URLs.
    parsed = urlsplit(callback_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
