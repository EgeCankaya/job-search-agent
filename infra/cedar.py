"""The Gateway's Cedar policies, first part (TOOL-4, OQ-36, OQ-44 j).

The Gateway authenticates callers with IAM, so each caller is an
`AgentCore::IamEntity` whose ID is its ARN with the session name removed:
a Lambda or the Runtime appears as `arn:aws:sts::<account>:assumed-role/<role>`
(AWS's policy examples). The first live deploy (2026-10-20) matched nothing
while the policies expected a session name, so each policy now names the
entity exactly, AWS's recommended form for one role.

- The agent (the Runtime's role) may call its five tools.
- The approval workflow's role may call `create_gmail_draft`, and only with
  an `approval_id`.
- No other IAM caller may call `create_gmail_draft`: an explicit forbid,
  which wins over any permit. Any other kind of caller is denied by default.

The engine denies everything not permitted, and each caller's tool list
shows only what it may call. Week 5 adds the never-send forbid (TOOL-2), the
recipient forbid (ALR-3), and the same files for cedarpy (MODE-4).
"""

from inbox_agent.tools import AGENT_TOOLS

DRAFT_TOOL = "create_gmail_draft"


def target_name(tool: str) -> str:
    return tool.replace("_", "-")


def action(tool: str) -> str:
    """The Gateway's action for a tool: `<target>___<tool>`."""
    return f'AgentCore::Action::"{target_name(tool)}___{tool}"'


def caller(role_name: str, account: str) -> str:
    """The Cedar entity for a role's callers, session name removed."""
    return f'AgentCore::IamEntity::"arn:aws:sts::{account}:assumed-role/{role_name}"'


def policies(*, gateway_arn: str, account: str, runtime_role: str, approval_role: str) -> dict[str, str]:
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
        # Scoped to IAM callers, the only kind this Gateway accepts: an
        # unscoped forbid also covers OAuth users, for whom it denies every
        # request, and AgentCore's validation refuses that as "overly
        # restrictive" (first deploy, 2026-10-20). Default deny covers the rest.
        "OnlyTheWorkflowDrafts": (
            "forbid(\n"
            "  principal is AgentCore::IamEntity,\n"
            f"  action == {action(DRAFT_TOOL)},\n"
            f"  resource == {gateway}\n"
            f") unless {{ principal == {workflow} }};"
        ),
    }
