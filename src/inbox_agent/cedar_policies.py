"""The Cedar policies, one set of files for both modes (TOOL-2, TOOL-4,
MODE-4, OQ-45 e).

Each policy is a committed file, `policies/<name>.cedar`. The principals and
the Gateway are placeholders, filled here:

- `?agent`: the agent, which may call its five tools;
- `?workflow`: the approval workflow, the only caller of `create_gmail_draft`;
- `?gateway`: the Gateway the policy applies to.

The actions are the Gateway's names, `<target>___<tool>`, in both modes.

For the Gateway the principals are the roles' IAM entities. The Gateway
authenticates callers with IAM, so a caller is an `AgentCore::IamEntity`
whose ID is its ARN with the session name removed:
`arn:aws:sts::<account>:assumed-role/<role>` (the first live deploy,
2026-10-20, matched nothing while the policies expected a session name). The
rendered text goes into the CloudFormation template, so no policy file ships
in a bundle.

For the local MCP server (MacBook mode) the worker is `local:agent`, and
cedarpy evaluates the same files from the repo before every tool call.
Anything but a clear Allow refuses the call: a deny, an evaluation error,
or no policies at all.

Whole-line `//` comments are the files' notes and are left out of the
rendered text.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# The repo's policies/ (src/inbox_agent/ is two levels down). `.parent` never
# fails, wherever a bundle puts this file.
POLICY_DIR = Path(__file__).resolve().parent.parent.parent / "policies"
# Where each policy was decided, for the Gateway's policy description.
DECIDED_IN = {
    "AgentTools": "OQ-44 (j)",
    "ApprovalDraft": "OQ-44 (j)",
    "OnlyTheWorkflowDrafts": "OQ-44 (j)",
    "NeverSend": "OQ-45 (a)",
    "NoRecipientKeys": "OQ-45 (b)",
}
PLACEHOLDERS = ("?agent", "?workflow", "?gateway")
LOCAL_AGENT = 'AgentCore::IamEntity::"local:agent"'
LOCAL_WORKFLOW = 'AgentCore::IamEntity::"local:workflow"'
LOCAL_GATEWAY = 'AgentCore::Gateway::"local"'
_PLACEHOLDER = re.compile(r"\?[A-Za-z_]\w*")


class PolicyRenderError(ValueError):
    pass


def target_name(tool: str) -> str:
    return tool.replace("_", "-")


def gateway_name(tool: str) -> str:
    """The Gateway's name for a tool: `<target>___<tool>`."""
    return f"{target_name(tool)}___{tool}"


def action(tool: str) -> str:
    return f'AgentCore::Action::"{gateway_name(tool)}"'


def caller(role_name: str, account: str) -> str:
    """The Cedar entity for a role's callers, session name removed."""
    return f'AgentCore::IamEntity::"arn:aws:sts::{account}:assumed-role/{role_name}"'


def policy_files(directory: Path = POLICY_DIR) -> dict[str, str]:
    """`{name: file text}`, in the order of DECIDED_IN (so the week 4
    policies keep their place), then by name; an empty or missing directory
    gives none."""
    if not directory.is_dir():
        return {}
    order = list(DECIDED_IN)
    paths = sorted(directory.glob("*.cedar"), key=lambda p: (order.index(p.stem) if p.stem in order else len(order), p.stem))
    return {path.stem: path.read_text(encoding="utf-8") for path in paths}


def render(files: dict[str, str], *, agent: str, workflow: str, gateway: str) -> dict[str, str]:
    """Each file with its notes removed and its placeholders filled. A
    placeholder left unfilled fails, so no half-rendered policy is used."""
    values = {"?agent": agent, "?workflow": workflow, "?gateway": gateway}
    rendered = {}
    for name, text in files.items():
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")).strip()
        # Placeholders are filled in one pass, so a value is never re-read.
        body = _PLACEHOLDER.sub(lambda m: values.get(m.group(0), m.group(0)), body)
        left = sorted({m.group(0) for m in _PLACEHOLDER.finditer(body)})
        if left or not body:
            raise PolicyRenderError(f"{name} has {'unknown placeholders' if left else 'no policy'}")
        rendered[name] = body
    return rendered


def gateway_policies(*, gateway_arn: str, account: str, runtime_role: str, approval_role: str, directory: Path = POLICY_DIR) -> dict[str, str]:
    return render(
        policy_files(directory),
        agent=caller(runtime_role, account),
        workflow=caller(approval_role, account),
        gateway=f'AgentCore::Gateway::"{gateway_arn}"',
    )


def local_policies(directory: Path = POLICY_DIR) -> dict[str, str]:
    return render(policy_files(directory), agent=LOCAL_AGENT, workflow=LOCAL_WORKFLOW, gateway=LOCAL_GATEWAY)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # The policies that decided: the permit for an Allow, the forbids for a
    # Deny, none for a default deny or an error.
    policy_ids: tuple[str, ...]
    errors: int = 0


class Authorizer:
    """cedarpy over rendered policies (MODE-4): one decision per tool call."""

    def __init__(self, policies: dict[str, str], *, gateway: str = LOCAL_GATEWAY) -> None:
        import cedarpy

        self._names = list(policies)
        self._gateway = gateway
        for name, text in policies.items():
            if len(cedarpy.PolicySet.from_str(text)) != 1:
                raise PolicyRenderError(f"{name} must hold exactly one policy")
        # Cedar numbers the policies of one text policy0, policy1, ... in order.
        self._set = cedarpy.PolicySet.from_str("\n".join(policies.values())) if policies else None

    def decide(self, principal: str, tool: str, arguments) -> Decision:
        import cedarpy

        if self._set is None:
            return Decision(False, ())
        request = {"principal": principal, "action": action(tool), "resource": self._gateway, "context": {"input": arguments}}
        try:
            result = cedarpy.is_authorized(request, self._set, [])
        except Exception:
            return Decision(False, (), errors=1)
        ids = tuple(self._name(policy) for policy in result.diagnostics.reasons)
        errors = len(result.diagnostics.errors)
        return Decision(result.decision == cedarpy.Decision.Allow and not errors, ids, errors)

    def _name(self, policy_id: str) -> str:
        number = policy_id.removeprefix("policy")
        return self._names[int(number)] if number.isdigit() and int(number) < len(self._names) else policy_id
