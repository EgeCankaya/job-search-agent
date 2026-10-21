"""The Gateway's Cedar policies (TOOL-2, TOOL-4, OQ-44 j, OQ-45 a, b, e).

The text lives in `policies/*.cedar`, shared with cedarpy on the local MCP
server (MODE-4); `inbox_agent.cedar_policies` fills in the roles and the
Gateway. The engine denies everything not permitted, and each caller's tool
list shows only what it may call.

- The agent (the Runtime's role) may call its five tools.
- The approval workflow's role may call `create_gmail_draft`, and only with
  an `approval_id`; no other IAM caller may (a forbid).
- No IAM caller may call a tool outside the six (`NeverSend`), or pass a
  recipient-like key to any tool (`NoRecipientKeys`).
"""

from inbox_agent.cedar_policies import action, caller, gateway_policies, target_name

DRAFT_TOOL = "create_gmail_draft"

__all__ = ["DRAFT_TOOL", "action", "caller", "policies", "target_name"]


def policies(*, gateway_arn: str, account: str, runtime_role: str, approval_role: str) -> dict[str, str]:
    return gateway_policies(gateway_arn=gateway_arn, account=account, runtime_role=runtime_role, approval_role=approval_role)
