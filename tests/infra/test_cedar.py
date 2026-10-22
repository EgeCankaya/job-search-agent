"""The Gateway's Cedar policies as text (TOOL-2, TOOL-4, OQ-44 j, OQ-45 a,
b, e), rendered from policies/*.cedar. The live denials are the week 4 exit
check 5 and the week 5 probe; tests/unit/test_cedar_policies.py runs the
rendered text in cedarpy (MODE-4)."""

import re

import pytest

from inbox_agent.cedar_policies import DECIDED_IN, PolicyRenderError, gateway_name, local_policies, policy_files, render
from inbox_agent.tools import AGENT_TOOLS, GATEWAY_TOOLS
from infra.cedar import DRAFT_TOOL, action, caller, policies

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


# Week 5 (OQ-45 e): the same text, now rendered from policies/*.cedar.
def week4_texts(*, gateway_arn, account, runtime_role, approval_role):
    """The week 4 code that built the deployed text, kept as the reference."""
    gateway = f'AgentCore::Gateway::"{gateway_arn}"'
    agent_actions = ", ".join(action(tool) for tool in AGENT_TOOLS)
    workflow = caller(approval_role, account)
    return {
        "AgentTools": (
            "permit(\n"
            f"  principal == {caller(runtime_role, account)},\n"
            f"  action in [{agent_actions}],\n"
            f"  resource == {gateway}\n"
            ");"
        ),
        "ApprovalDraft": (
            "permit(\n"
            f"  principal == {workflow},\n"
            f"  action == {action(DRAFT_TOOL)},\n"
            f"  resource == {gateway}\n"
            ") when { context.input has approval_id };"
        ),
        "OnlyTheWorkflowDrafts": (
            "forbid(\n"
            "  principal is AgentCore::IamEntity,\n"
            f"  action == {action(DRAFT_TOOL)},\n"
            f"  resource == {gateway}\n"
            f") unless {{ principal == {workflow} }};"
        ),
    }


def test_the_week_4_policies_render_to_the_deployed_text_byte_for_byte():
    args = dict(gateway_arn=GATEWAY, account="111122223333", runtime_role="RuntimeRole123", approval_role="ApprovalRole456")
    expected = week4_texts(**args)
    rendered = policies(**args)
    assert {name: rendered[name] for name in expected} == expected
    # The week 4 policies keep their place; the week 5 forbids follow.
    assert list(rendered) == ["AgentTools", "ApprovalDraft", "OnlyTheWorkflowDrafts", "NeverSend", "NoRecipientKeys"]
    assert list(rendered) == list(DECIDED_IN)


def actions_in(text):
    return [name for name in re.findall(r'AgentCore::Action::"([^"]+)"', text)]


def test_the_action_lists_in_the_files_are_the_tools_in_tools_py():
    files = policy_files()
    assert actions_in(files["AgentTools"]) == [gateway_name(tool) for tool in AGENT_TOOLS]
    assert actions_in(files["NeverSend"]) == [gateway_name(tool) for tool in GATEWAY_TOOLS]
    assert len(GATEWAY_TOOLS) == 6
    for name in ("ApprovalDraft", "OnlyTheWorkflowDrafts"):
        assert actions_in(files[name]) == [gateway_name(DRAFT_TOOL)]
    # Named, not every action: a forbid on future tools is "Overly Restrictive".
    assert actions_in(files["NoRecipientKeys"]) == [gateway_name(tool) for tool in GATEWAY_TOOLS]


def test_the_recipient_forbid_names_the_seven_keys_behind_a_guard():
    text = policy_files()["NoRecipientKeys"]
    keys = re.findall(r"context\.input has (\w+)", text)
    assert keys == ["to", "recipient", "recipients", "phone", "email", "cc", "bcc"]
    # Validation refuses an unguarded access to the optional `input` (run 39775820712).
    assert "when { context has input && (" in text


def test_every_forbid_is_scoped_to_iam_callers():
    # An unscoped forbid fails AgentCore's validation (week 4).
    forbids = {name: text for name, text in TEXTS.items() if text.startswith("forbid(")}
    assert set(forbids) == {"OnlyTheWorkflowDrafts", "NeverSend", "NoRecipientKeys"}
    assert all(text.startswith("forbid(\n  principal is AgentCore::IamEntity,\n") for text in forbids.values())


def test_the_files_hold_placeholders_not_accounts_or_roles():
    for name, text in policy_files().items():
        assert "arn:" not in text and "assumed-role" not in text and 'Gateway::"' not in text, name
        assert "?gateway" in text, name


def test_notes_are_left_out_of_the_rendered_text():
    assert all("//" not in text for text in TEXTS.values())


@pytest.mark.parametrize(
    "text",
    [
        "permit(principal == ?someone, action, resource == ?gateway);",
        "permit(principal == ?agentx, action, resource == ?gateway);",
        "// only a note\n",
    ],
)
def test_a_placeholder_left_unfilled_or_an_empty_file_fails_the_render(text):
    with pytest.raises(PolicyRenderError):
        render({"Broken": text}, agent='X::"a"', workflow='X::"w"', gateway='X::"g"')


def test_render_fills_every_placeholder_once():
    out = render({"P": "permit(principal == ?agent, action, resource == ?gateway);"}, agent='X::"a"', workflow='X::"w"', gateway='X::"g"')
    assert out == {"P": 'permit(principal == X::"a", action, resource == X::"g");'}


def test_the_local_policies_use_the_local_entities(tmp_path):
    local = local_policies()
    assert set(local) == set(DECIDED_IN)
    assert 'principal == AgentCore::IamEntity::"local:agent"' in local["AgentTools"]
    assert 'AgentCore::IamEntity::"local:workflow"' in local["ApprovalDraft"]
    assert all('resource == AgentCore::Gateway::"local"' in text for text in local.values())
    assert local_policies(tmp_path) == {} and local_policies(tmp_path / "missing") == {}
