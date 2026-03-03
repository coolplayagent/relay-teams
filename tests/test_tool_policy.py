from agent_teams.tools.policy import ToolApprovalPolicy


def test_default_policy_requires_high_risk_tools() -> None:
    policy = ToolApprovalPolicy()
    assert policy.requires_approval('shell')
    assert policy.requires_approval('write')
    assert policy.requires_approval('write_stage_doc')
    assert not policy.requires_approval('read')
