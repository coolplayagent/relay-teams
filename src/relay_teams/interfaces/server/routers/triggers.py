# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.public_webhook_url import (
    build_public_base_url_path,
    is_public_http_url,
)
from relay_teams.interfaces.server.deps import (
    get_github_config_service,
    get_github_trigger_service,
)
from relay_teams.triggers import (
    GitHubRepoSubscriptionConflictError,
    GitHubAvailableRepositoryRecord,
    GitHubRepoSubscriptionCreateInput,
    GitHubRepoSubscriptionRecord,
    GitHubRepoSubscriptionUpdateInput,
    GitHubApiError,
    GitHubTriggerAccountCreateInput,
    GitHubTriggerAccountNameConflictError,
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountUpdateInput,
    GitHubTriggerService,
    TriggerRuleCreateInput,
    TriggerRuleNameConflictError,
    TriggerRuleRecord,
    TriggerRuleUpdateInput,
)
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/triggers", tags=["Triggers"])


def _raise_for_github_api_error(exc: GitHubApiError) -> NoReturn:
    if exc.status_code == 404:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _github_delivery_callback_url(
    request: Request,
    github_config_service: GitHubConfigService,
) -> str | None:
    configured_base_url = github_config_service.get_github_config().webhook_base_url
    if configured_base_url is not None:
        return build_public_base_url_path(
            configured_base_url,
            "/api/triggers/github/deliveries",
        )
    request_callback_url = str(request.url_for("handle_github_delivery"))
    if is_public_http_url(request_callback_url):
        return request_callback_url
    return None


@router.get("/github/accounts", response_model=list[GitHubTriggerAccountRecord])
async def list_github_accounts(
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> list[GitHubTriggerAccountRecord]:
    return list(service.list_accounts())


@router.post("/github/accounts", response_model=GitHubTriggerAccountRecord)
async def create_github_account(
    req: GitHubTriggerAccountCreateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubTriggerAccountRecord:
    try:
        return service.create_account(req)
    except GitHubTriggerAccountNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/github/accounts/{account_id}", response_model=GitHubTriggerAccountRecord
)
async def update_github_account(
    account_id: RequiredIdentifierStr,
    req: GitHubTriggerAccountUpdateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubTriggerAccountRecord:
    try:
        return service.update_account(account_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubTriggerAccountNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/github/accounts/{account_id}")
async def delete_github_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> dict[str, JsonValue]:
    try:
        service.delete_account(account_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/github/accounts/{account_id}:enable", response_model=GitHubTriggerAccountRecord
)
async def enable_github_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubTriggerAccountRecord:
    try:
        return service.enable_account(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/github/accounts/{account_id}:disable", response_model=GitHubTriggerAccountRecord
)
async def disable_github_account(
    account_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubTriggerAccountRecord:
    try:
        return service.disable_account(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/github/repos", response_model=list[GitHubRepoSubscriptionRecord])
async def list_github_repo_subscriptions(
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> list[GitHubRepoSubscriptionRecord]:
    return list(service.list_repo_subscriptions())


@router.get(
    "/github/accounts/{account_id}/repositories",
    response_model=list[GitHubAvailableRepositoryRecord],
)
async def list_github_available_repositories(
    account_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
    query: str | None = None,
) -> list[GitHubAvailableRepositoryRecord]:
    try:
        return list(service.list_available_repositories(account_id, query=query))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/github/repos", response_model=GitHubRepoSubscriptionRecord)
async def create_github_repo_subscription(
    request: Request,
    req: GitHubRepoSubscriptionCreateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
    github_config_service: Annotated[
        GitHubConfigService, Depends(get_github_config_service)
    ],
) -> GitHubRepoSubscriptionRecord:
    try:
        resolved_req = req
        if req.callback_url is None or not req.callback_url.strip():
            callback_url = _github_delivery_callback_url(request, github_config_service)
            if callback_url is not None:
                resolved_req = req.model_copy(update={"callback_url": callback_url})
        return await run_in_threadpool(service.create_repo_subscription, resolved_req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubRepoSubscriptionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/github/repos/{repo_subscription_id}",
    response_model=GitHubRepoSubscriptionRecord,
)
async def update_github_repo_subscription(
    repo_subscription_id: RequiredIdentifierStr,
    req: GitHubRepoSubscriptionUpdateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubRepoSubscriptionRecord:
    try:
        return service.update_repo_subscription(repo_subscription_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubRepoSubscriptionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/github/repos/{repo_subscription_id}")
async def delete_github_repo_subscription(
    repo_subscription_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> dict[str, JsonValue]:
    try:
        service.delete_repo_subscription(repo_subscription_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/github/repos/{repo_subscription_id}:enable",
    response_model=GitHubRepoSubscriptionRecord,
)
async def enable_github_repo_subscription(
    repo_subscription_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubRepoSubscriptionRecord:
    try:
        return service.enable_repo_subscription(repo_subscription_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/github/repos/{repo_subscription_id}:disable",
    response_model=GitHubRepoSubscriptionRecord,
)
async def disable_github_repo_subscription(
    repo_subscription_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> GitHubRepoSubscriptionRecord:
    try:
        return service.disable_repo_subscription(repo_subscription_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/github/rules", response_model=list[TriggerRuleRecord])
async def list_github_rules(
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> list[TriggerRuleRecord]:
    return list(service.list_rules())


@router.post("/github/rules", response_model=TriggerRuleRecord)
async def create_github_rule(
    req: TriggerRuleCreateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> TriggerRuleRecord:
    try:
        return service.create_rule(req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriggerRuleNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/github/rules/{trigger_rule_id}", response_model=TriggerRuleRecord)
async def update_github_rule(
    trigger_rule_id: RequiredIdentifierStr,
    req: TriggerRuleUpdateInput,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> TriggerRuleRecord:
    try:
        return service.update_rule(trigger_rule_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriggerRuleNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/github/rules/{trigger_rule_id}")
async def delete_github_rule(
    trigger_rule_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> dict[str, JsonValue]:
    try:
        service.delete_rule(trigger_rule_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/github/rules/{trigger_rule_id}:enable", response_model=TriggerRuleRecord)
async def enable_github_rule(
    trigger_rule_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> TriggerRuleRecord:
    try:
        return service.enable_rule(trigger_rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/github/rules/{trigger_rule_id}:disable", response_model=TriggerRuleRecord
)
async def disable_github_rule(
    trigger_rule_id: RequiredIdentifierStr,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> TriggerRuleRecord:
    try:
        return service.disable_rule(trigger_rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubApiError as exc:
        _raise_for_github_api_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/github/deliveries")
async def handle_github_delivery(
    request: Request,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> dict[str, JsonValue]:
    body = await request.body()
    headers = {str(key): str(value) for key, value in request.headers.items()}
    return await run_in_threadpool(
        service.handle_inbound_github_delivery,
        headers=headers,
        body=body,
    )
