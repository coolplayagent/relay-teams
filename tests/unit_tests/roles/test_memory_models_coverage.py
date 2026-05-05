# -*- coding: utf-8 -*-
"""Coverage for memory_models.py VerificationPassRate edge cases."""

from __future__ import annotations


from relay_teams.roles.memory_models import VerificationPassRate


def test_verification_pass_rate_zero_denominator() -> None:
    rate = VerificationPassRate(
        total_verifications=0, passed_verifications=0, pass_rate=0.0
    )
    assert rate.pass_rate == 0.0
