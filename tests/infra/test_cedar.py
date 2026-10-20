"""The week 4 Cedar policies as text (TOOL-4, OQ-44 j). The live denial is
the week 4 exit check 5; cedarpy runs these files from week 5 (MODE-4)."""

from infra.cedar import DRAFT_TOOL, action, policies
from inbox_agent.tools import AGENT_TOOLS, GATEWAY_TOOLS

GATEWAY = "arn:aws:bedrock-agentcore:eu-north-1:111122223333:gateway/inbox-agent-abcdefghij"
TEXTS = policies(gateway_arn=GATEWAY, account="111122223333", runtime_role="RuntimeRole123", approval_role="ApprovalRole456")


def test_the_agent_is_permitted_its_five_tools_and_not_the_draft_tool():
    agent = TEXTS["AgentTools"]
    assert agent.startswith("permit(")
    assert all(action(tool) in agent for tool in AGENT_TOOLS)
    assert action(DRAFT_TOOL) not in agent
    assert set(GATEWAY_TOOLS) - set(AGENT_TOOLS) == {DRAFT_TOOL}
    # The entity ID has no session name (AWS's examples; the first live deploy).
    assert 'principal == AgentCore::IamEntity::"arn:aws:sts::111122223333:assumed-role/RuntimeRole123",' in agent
    assert "ApprovalRole456" not in agent and "*" not in agent


def test_only_the_workflow_may_draft_and_only_with_an_approval_id():
    permit = TEXTS["ApprovalDraft"]
    assert permit.startswith("permit(") and action(DRAFT_TOOL) in permit
    assert "context.input has approval_id" in permit
    assert 'principal == AgentCore::IamEntity::"arn:aws:sts::111122223333:assumed-role/ApprovalRole456",' in permit
    assert "RuntimeRole123" not in permit
    forbid = TEXTS["OnlyTheWorkflowDrafts"]
    # Scoped to IAM callers: an unscoped forbid fails AgentCore's validation.
    assert forbid.startswith("forbid(\n  principal is AgentCore::IamEntity,") and action(DRAFT_TOOL) in forbid
    assert 'unless { principal == AgentCore::IamEntity::"arn:aws:sts::111122223333:assumed-role/ApprovalRole456" }' in forbid
    assert "RuntimeRole123" not in forbid


def test_every_policy_names_this_gateway():
    assert all(f'resource == AgentCore::Gateway::"{GATEWAY}"' in text for text in TEXTS.values())


def test_actions_use_the_gateway_target_names():
    assert action("create_gmail_draft") == 'AgentCore::Action::"create-gmail-draft___create_gmail_draft"'


def test_no_policy_expects_a_session_name():
    assert all("assumed-role/RuntimeRole123/" not in t and "assumed-role/ApprovalRole456/" not in t for t in TEXTS.values())
