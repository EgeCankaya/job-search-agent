"""The Gateway's Cedar policies, first part (TOOL-4, OQ-36, OQ-44 j).

The Gateway authenticates callers with IAM, so each caller is an
`AgentCore::IamEntity` whose `id` is its IAM ARN. A Lambda or the Runtime
calls with a role session, `arn:aws:sts::<account>:assumed-role/<role>/<session>`;
the role ARN form is matched too, in case the Gateway reports that one.

- The agent (the Runtime's role) may call its five tools.
- The approval workflow's role may call `create_gmail_draft`, and only with
  an `approval_id`.
- No other IAM caller may call `create_gmail_draft`: an explicit forbid,
  which wins over any permit. Any other kind of caller is denied by default.

The engine denies everything not permitted. Week 5 adds the never-send forbid
(TOOL-2), the recipient forbid (ALR-3), and the same files for cedarpy
(MODE-4).
"""

from inbox_agent.tools import AGENT_TOOLS

DRAFT_TOOL = "create_gmail_draft"


def target_name(tool: str) -> str:
    return tool.replace("_", "-")


def action(tool: str) -> str:
    """The Gateway's action for a tool: `<target>___<tool>`."""
    return f'AgentCore::Action::"{target_name(tool)}___{tool}"'


def caller_is(role_name: str, account: str) -> str:
    return (
        f'(principal.id like "arn:aws:sts::{account}:assumed-role/{role_name}/*" || '
        f'principal.id == "arn:aws:iam::{account}:role/{role_name}")'
    )


def policies(*, gateway_arn: str, account: str, runtime_role: str, approval_role: str) -> dict[str, str]:
    gateway = f'AgentCore::Gateway::"{gateway_arn}"'
    agent_actions = ", ".join(action(tool) for tool in AGENT_TOOLS)
    return {
        "AgentTools": (
            "permit(\n"
            "  principal is AgentCore::IamEntity,\n"
            f"  action in [{agent_actions}],\n"
            f"  resource == {gateway}\n"
            f") when {{ {caller_is(runtime_role, account)} }};"
        ),
        "ApprovalDraft": (
            "permit(\n"
            "  principal is AgentCore::IamEntity,\n"
            f"  action == {action(DRAFT_TOOL)},\n"
            f"  resource == {gateway}\n"
            f") when {{ {caller_is(approval_role, account)} && context.input has approval_id }};"
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
            f") unless {{ {caller_is(approval_role, account)} }};"
        ),
    }
