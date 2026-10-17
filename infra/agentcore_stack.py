"""The AgentCore stack (MODE-2): Runtime, Gateway, Memory and Evaluations.

It is deployed only when AGENT_MODE=cloud, so switching to MacBook mode removes
these paid resources and leaves the always-free stack untouched. Empty in
week 1; the resources arrive from week 3.
"""

from aws_cdk import Stack
from constructs import Construct


class AgentCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
