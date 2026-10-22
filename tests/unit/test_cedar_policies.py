"""The policies' effect, in cedarpy over the exact text CloudFormation gets
(TOOL-2, TOOL-4, ALR-3, OQ-45 a to c, e). AgentCore runs the same text in
ENFORCE; the live probe is the ground truth for the Gateway."""

import pytest

from inbox_agent.cedar_policies import (
    LOCAL_AGENT,
    LOCAL_WORKFLOW,
    Authorizer,
    PolicyRenderError,
    caller,
    local_policies,
)
from inbox_agent.tools import AGENT_TOOLS, GATEWAY_TOOLS
from infra.cedar import policies

ACCOUNT = "111122223333"
GATEWAY_ARN = f"arn:aws:bedrock-agentcore:eu-north-1:{ACCOUNT}:gateway/inbox-agent-abcdefghij"
AGENT = caller("RuntimeRole123", ACCOUNT)
WORKFLOW = caller("ApprovalRole456", ACCOUNT)
OWNER = caller("AccountFullAccessRole", ACCOUNT)  # my aws login session
RECIPIENT_KEYS = ("to", "recipient", "recipients", "phone", "email", "cc", "bcc")
VALID = {
    "read_email": {"message_id": "19a0c0ffee"},
    "upsert_application": {"company": "Örnek Test Ltd", "role": "Backend Engineer", "status": "applied"},
    "list_applications": {"status": "open"},
    "notify_owner": {"kind": "urgent", "email_class": "interview_invite", "company": "Örnek Test Ltd"},
    "search_gmail": {"company": "Örnek Test Ltd"},
    "create_gmail_draft": {"approval_id": "0" * 32},
}


@pytest.fixture(scope="module")
def gateway():
    texts = policies(gateway_arn=GATEWAY_ARN, account=ACCOUNT, runtime_role="RuntimeRole123", approval_role="ApprovalRole456")
    return Authorizer(texts, gateway=f'AgentCore::Gateway::"{GATEWAY_ARN}"')


def test_the_valid_inputs_pass_the_tools_own_check():
    from inbox_agent.tools import TOOL_SPECS, validate

    assert set(VALID) == set(GATEWAY_TOOLS)
    for name, arguments in VALID.items():
        validate(TOOL_SPECS[name], arguments)


@pytest.mark.parametrize("tool", AGENT_TOOLS)
def test_the_agent_may_call_each_of_its_five_tools(gateway, tool):
    decision = gateway.decide(AGENT, tool, VALID[tool])
    assert decision.allowed and decision.policy_ids == ("AgentTools",)


def test_the_agent_may_not_draft(gateway):
    decision = gateway.decide(AGENT, "create_gmail_draft", VALID["create_gmail_draft"])
    assert not decision.allowed and decision.policy_ids == ("OnlyTheWorkflowDrafts",)


@pytest.mark.parametrize("tool", ["send_email", "forward_email", "send_whatsapp", "delete_email"])
@pytest.mark.parametrize("principal", [AGENT, WORKFLOW, OWNER])
def test_no_caller_may_call_a_tool_outside_the_six(gateway, tool, principal):
    decision = gateway.decide(principal, tool, {})
    assert not decision.allowed and decision.policy_ids == ("NeverSend",)


@pytest.mark.parametrize("key", RECIPIENT_KEYS)
def test_notify_owner_with_a_recipient_key_is_denied(gateway, key):
    decision = gateway.decide(AGENT, "notify_owner", {**VALID["notify_owner"], key: "someone@example.com"})
    assert not decision.allowed and decision.policy_ids == ("NoRecipientKeys",)


@pytest.mark.parametrize("tool", AGENT_TOOLS)
def test_any_agent_tool_with_a_recipient_key_is_denied(gateway, tool):
    decision = gateway.decide(AGENT, tool, {**VALID[tool], "to": "someone@example.com"})
    assert not decision.allowed and "NoRecipientKeys" in decision.policy_ids


def test_the_workflow_may_draft_only_with_an_approval_id_and_no_recipient(gateway):
    allowed = gateway.decide(WORKFLOW, "create_gmail_draft", VALID["create_gmail_draft"])
    assert allowed.allowed and allowed.policy_ids == ("ApprovalDraft",)
    assert not gateway.decide(WORKFLOW, "create_gmail_draft", {}).allowed
    cc = gateway.decide(WORKFLOW, "create_gmail_draft", {**VALID["create_gmail_draft"], "cc": "someone@example.com"})
    assert not cc.allowed and cc.policy_ids == ("NoRecipientKeys",)


@pytest.mark.parametrize("tool", [t for t in GATEWAY_TOOLS if t != "create_gmail_draft"])
def test_the_workflow_may_call_nothing_else(gateway, tool):
    assert not gateway.decide(WORKFLOW, tool, VALID[tool]).allowed


@pytest.mark.parametrize("tool", GATEWAY_TOOLS)
def test_my_session_is_denied_every_call(gateway, tool):
    assert not gateway.decide(OWNER, tool, VALID[tool]).allowed


def test_a_caller_with_a_session_name_matches_nothing(gateway):
    # The Gateway removes the session name; a policy expecting one matched nobody.
    assert not gateway.decide(caller("RuntimeRole123/session-1", ACCOUNT), "list_applications", {}).allowed


def test_the_local_policies_decide_the_same_for_the_local_entities():
    local = Authorizer(local_policies())
    assert local.decide(LOCAL_AGENT, "list_applications", {}).allowed
    assert local.decide(LOCAL_WORKFLOW, "create_gmail_draft", VALID["create_gmail_draft"]).allowed
    assert local.decide(LOCAL_AGENT, "create_gmail_draft", VALID["create_gmail_draft"]).policy_ids == ("OnlyTheWorkflowDrafts",)
    assert local.decide(LOCAL_AGENT, "send_email", {}).policy_ids == ("NeverSend",)
    upsert = local.decide(LOCAL_AGENT, "upsert_application", {"company": "Örnek Test Ltd", "to": "someone@example.com"})
    assert not upsert.allowed and upsert.policy_ids == ("NoRecipientKeys",)


def test_no_policies_deny_everything():
    decision = Authorizer({}).decide(LOCAL_AGENT, "list_applications", {})
    assert not decision.allowed and decision.policy_ids == ()


def test_an_input_cedar_cannot_hold_is_refused_not_allowed():
    # JSON null and floats have no Cedar value: an error, so a refusal.
    local = Authorizer(local_policies())
    assert not local.decide(LOCAL_AGENT, "list_applications", {"company": None}).allowed
    assert not local.decide(LOCAL_AGENT, "list_applications", {"min_days_since_contact": 1.5}).allowed


def test_a_file_with_two_policies_is_refused():
    with pytest.raises(PolicyRenderError):
        Authorizer({"Two": 'permit(principal, action, resource);\nforbid(principal, action, resource);'})


def test_a_request_without_an_input_is_not_an_error_for_the_recipient_forbid(gateway):
    # AgentCore's own actions may carry no input; the guard keeps evaluation clean.
    import cedarpy

    texts = policies(gateway_arn=GATEWAY_ARN, account=ACCOUNT, runtime_role="RuntimeRole123", approval_role="ApprovalRole456")
    request = {
        "principal": AGENT,
        "action": 'AgentCore::Action::"InvokeAgent"',
        "resource": f'AgentCore::Gateway::"{GATEWAY_ARN}"',
        "context": {},
    }
    result = cedarpy.is_authorized(request, texts["NoRecipientKeys"], [])
    assert result.diagnostics.errors == [] and result.diagnostics.reasons == []
