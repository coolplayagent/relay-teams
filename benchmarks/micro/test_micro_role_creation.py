# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import ValidationError

from relay_teams.roles.role_models import RoleDefinition


def _parse_roles(data: list[dict[str, object]]) -> list[RoleDefinition]:
    return [RoleDefinition.model_validate(d) for d in data]


def test_micro_role_creation_100(benchmark, role_json_data_100):
    result = benchmark(_parse_roles, role_json_data_100)
    assert len(result) == 100


def test_micro_role_creation_500(benchmark, role_json_data_500):
    result = benchmark(_parse_roles, role_json_data_500)
    assert len(result) == 500


def test_micro_role_creation_1000(benchmark, role_json_data_1000):
    result = benchmark(_parse_roles, role_json_data_1000)
    assert len(result) == 1000


def test_micro_role_validation_rejects_invalid(benchmark, role_json_data_100):
    invalid = list(role_json_data_100)
    invalid[0] = {
        "role_id": "",
        "name": "",
        "description": "",
        "version": "",
        "system_prompt": "",
    }

    def _validate_and_count() -> int:
        count = 0
        for d in invalid:
            try:
                RoleDefinition.model_validate(d)
            except ValidationError:
                count += 1
        return count

    errors = benchmark(_validate_and_count)
    assert errors >= 1
