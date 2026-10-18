"""Builds the CDK app for the configured AGENT_MODE (MODE-1, MODE-2)."""

import os

from aws_cdk import Annotations, App, Environment, Stack, Tags

from infra import config
from infra.agentcore_stack import AgentCoreStack
from infra.always_free_stack import AlwaysFreeStack

ALWAYS_FREE_STACK = "InboxAgentAlwaysFree"
AGENTCORE_STACK = "InboxAgentAgentCore"


def build(app: App) -> dict[str, Stack]:
    mode = config.agent_mode(app)
    env = Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.REGION)

    stacks: dict[str, Stack] = {
        ALWAYS_FREE_STACK: AlwaysFreeStack(
            app, ALWAYS_FREE_STACK, env=env, alert_email=config.alert_email(app), agent_mode=mode
        )
    }
    if mode == "cloud":
        stacks[AGENTCORE_STACK] = AgentCoreStack(app, AGENTCORE_STACK, env=env)
    else:
        Annotations.of(stacks[ALWAYS_FREE_STACK]).add_warning_v2(
            "inbox-agent:local-mode-incomplete",
            "AGENT_MODE=local: the MacBook-mode local worker that replaces AgentCore Runtime is not built yet. "
            f"An already deployed {AGENTCORE_STACK} is only removed by `cdk destroy {AGENTCORE_STACK}`.",
        )

    Tags.of(app).add(*config.PROJECT_TAG)
    return stacks
