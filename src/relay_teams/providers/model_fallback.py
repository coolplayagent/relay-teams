# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event
from relay_teams.providers.llm_retry import LlmRetryErrorInfo
from relay_teams.providers.model_config import (
    ModelEndpointConfig,
    ModelFallbackConfig,
    ModelFallbackPolicy,
    ModelFallbackStrategy,
    ProviderType,
)

LOGGER = get_logger(__name__)


class ProfileCooldownRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_name: str = Field(min_length=1)
    cooldown_until: datetime
    reason: str = ""


class LlmFallbackDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    policy_id: str = Field(min_length=1)
    from_profile_name: str = Field(min_length=1)
    to_profile_name: str = Field(min_length=1)
    from_provider: ProviderType
    to_provider: ProviderType
    from_model: str = Field(min_length=1)
    to_model: str = Field(min_length=1)
    hop: int = Field(ge=1)
    reason: str = ""
    cooldown_until: datetime
    target_config: ModelEndpointConfig


class DisabledLlmFallbackMiddleware:
    def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
        _ = config
        return False

    def select_fallback(
        self,
        *,
        current_profile_name: str | None,
        current_config: ModelEndpointConfig,
        error: LlmRetryErrorInfo,
        visited_profiles: Sequence[str],
        hop: int,
    ) -> LlmFallbackDecision | None:
        _ = (
            current_profile_name,
            current_config,
            error,
            visited_profiles,
            hop,
        )
        return None


class ProfileCooldownRegistry:
    def __init__(self) -> None:
        self._records: dict[str, ProfileCooldownRecord] = {}
        self._lock = Lock()

    def get(self, profile_name: str) -> ProfileCooldownRecord | None:
        normalized_name = profile_name.strip()
        if not normalized_name:
            return None
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            record = self._records.get(normalized_name)
            if record is None:
                return None
            if record.cooldown_until <= now:
                self._records.pop(normalized_name, None)
                return None
            return record

    def is_cooling(self, profile_name: str) -> bool:
        return self.get(profile_name) is not None

    def set_cooldown(
        self,
        *,
        profile_name: str,
        delay_ms: int,
        reason: str,
    ) -> ProfileCooldownRecord:
        normalized_name = profile_name.strip()
        cooldown_until = datetime.now(tz=timezone.utc) + timedelta(
            milliseconds=max(0, delay_ms)
        )
        record = ProfileCooldownRecord(
            profile_name=normalized_name,
            cooldown_until=cooldown_until,
            reason=reason,
        )
        with self._lock:
            self._records[normalized_name] = record
        return record


class LlmFallbackMiddleware:
    def __init__(
        self,
        *,
        get_fallback_config: Callable[[], ModelFallbackConfig],
        get_profiles: Callable[[], dict[str, ModelEndpointConfig]],
        cooldown_registry: ProfileCooldownRegistry | None = None,
    ) -> None:
        self._get_fallback_config = get_fallback_config
        self._get_profiles = get_profiles
        self._cooldown_registry = cooldown_registry or ProfileCooldownRegistry()

    def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
        policy = self._get_fallback_config().get_policy(config.fallback_policy_id)
        return policy is not None and policy.enabled

    def select_fallback(
        self,
        *,
        current_profile_name: str | None,
        current_config: ModelEndpointConfig,
        error: LlmRetryErrorInfo,
        visited_profiles: Sequence[str],
        hop: int,
    ) -> LlmFallbackDecision | None:
        if current_profile_name is None:
            return None
        normalized_current_profile = current_profile_name.strip()
        if not normalized_current_profile:
            return None
        if not error.rate_limited:
            return None
        fallback_config = self._get_fallback_config()
        policy = fallback_config.get_policy(current_config.fallback_policy_id)
        if policy is None or not policy.enabled:
            return None
        if hop >= policy.max_hops:
            return None
        cooldown_record = self._cooldown_registry.set_cooldown(
            profile_name=normalized_current_profile,
            delay_ms=_resolve_cooldown_ms(error=error, policy=policy),
            reason=error.error_code or error.error_type or "rate_limited",
        )
        candidate = self._select_candidate(
            current_profile_name=normalized_current_profile,
            current_config=current_config,
            policy=policy,
            visited_profiles=visited_profiles,
        )
        if candidate is None:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.fallback.no_candidate",
                message="No fallback candidate available after LLM rate limit.",
                payload={
                    "profile_name": normalized_current_profile,
                    "policy_id": policy.policy_id,
                    "provider": current_config.provider,
                    "model": current_config.model,
                    "visited_profiles": list(visited_profiles),
                    "cooldown_until": cooldown_record.cooldown_until.isoformat(),
                },
            )
            return None
        target_profile_name, target_config = candidate
        return LlmFallbackDecision(
            policy_id=policy.policy_id,
            from_profile_name=normalized_current_profile,
            to_profile_name=target_profile_name,
            from_provider=current_config.provider,
            to_provider=target_config.provider,
            from_model=current_config.model,
            to_model=target_config.model,
            hop=hop + 1,
            reason=error.error_code or error.error_type or "rate_limited",
            cooldown_until=cooldown_record.cooldown_until,
            target_config=target_config,
        )

    def _select_candidate(
        self,
        *,
        current_profile_name: str,
        current_config: ModelEndpointConfig,
        policy: ModelFallbackPolicy,
        visited_profiles: Sequence[str],
    ) -> tuple[str, ModelEndpointConfig] | None:
        visited = {
            profile_name.strip() for profile_name in visited_profiles if profile_name
        }
        current_provider = current_config.provider
        same_provider_candidates: list[tuple[str, ModelEndpointConfig]] = []
        other_provider_candidates: list[tuple[str, ModelEndpointConfig]] = []
        for profile_name, profile_config in self._get_profiles().items():
            if profile_name == current_profile_name:
                continue
            if profile_name in visited:
                continue
            if profile_config.provider == ProviderType.ECHO:
                continue
            if profile_config.fallback_priority <= 0:
                continue
            if self._cooldown_registry.is_cooling(profile_name):
                continue
            candidate = (profile_name, profile_config)
            if profile_config.provider == current_provider:
                same_provider_candidates.append(candidate)
            else:
                other_provider_candidates.append(candidate)
        same_provider_candidates.sort(key=_sort_candidates)
        other_provider_candidates.sort(key=_sort_candidates)
        ordered_candidates = _merge_candidates_by_policy(
            policy=policy,
            same_provider_candidates=same_provider_candidates,
            other_provider_candidates=other_provider_candidates,
        )
        if not ordered_candidates:
            return None
        return ordered_candidates[0]


def _sort_candidates(candidate: tuple[str, ModelEndpointConfig]) -> tuple[int, str]:
    profile_name, config = candidate
    return (-config.fallback_priority, profile_name.casefold())


def _merge_candidates_by_policy(
    *,
    policy: ModelFallbackPolicy,
    same_provider_candidates: Sequence[tuple[str, ModelEndpointConfig]],
    other_provider_candidates: Sequence[tuple[str, ModelEndpointConfig]],
) -> tuple[tuple[str, ModelEndpointConfig], ...]:
    if policy.strategy == ModelFallbackStrategy.OTHER_PROVIDER_ONLY:
        return tuple(other_provider_candidates)
    return tuple(same_provider_candidates) + tuple(other_provider_candidates)


def _resolve_cooldown_ms(
    *,
    error: LlmRetryErrorInfo,
    policy: ModelFallbackPolicy,
) -> int:
    if error.retry_after_ms is not None:
        return error.retry_after_ms
    return policy.cooldown_seconds * 1000
