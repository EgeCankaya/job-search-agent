"""Settings shared by the CDK app and its tests.

Nothing personal lives here. The owner's email address comes from CDK context
or the environment at synth time.
"""

import os
import re

from constructs import Construct

PROJECT_TAG = ("project", "inbox-agent")
# The account uses AWS's new sign-up experience, which pins every project to
# a home Region picked from the contact country: Turkey -> Stockholm (OQ-39 f).
REGION = "eu-north-1"

AGENT_MODES = ("cloud", "local")

BUDGET_LIMITS_USD = (1, 5, 10)
BUDGET_NAME_PREFIX = "inbox-agent"


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _setting(scope: Construct, key: str, env_var: str) -> str | None:
    value = scope.node.try_get_context(key) or os.environ.get(env_var)
    return value.strip() if isinstance(value, str) else value


def agent_mode(scope: Construct) -> str:
    """AGENT_MODE=cloud|local (MODE-1). Defaults to cloud."""
    mode = (_setting(scope, "agent_mode", "AGENT_MODE") or "cloud").lower()
    if mode not in AGENT_MODES:
        raise ValueError(f"AGENT_MODE must be one of {AGENT_MODES}, got {mode!r}")
    return mode


def alert_email(scope: Construct) -> str:
    """The owner's address, for budget alerts and the SNS alert subscription (ALR-3).

    Supplied at synth time, never committed.
    """
    email = _setting(scope, "alert_email", "ALERT_EMAIL")
    # A whole address, checked here: SNS rejects a broken one only mid-deploy,
    # and the subscription's construct ID comes from it, so a typo also tries
    # to replace the confirmed subscription.
    if not email or not _EMAIL.match(email):
        raise ValueError(
            "Set the owner's whole alert address: `cdk synth -c alert_email=you@example.com` "
            "or the ALERT_EMAIL environment variable."
        )
    return email

# ING-4: AgentCore Identity resources the owner made by hand (2026-10-18).
# Keep in step with src/inbox_agent/settings.py; a test checks they match.
GOOGLE_CREDENTIAL_PROVIDER = "inbox-agent-google"
POLLER_WORKLOAD_IDENTITY = "inbox-agent-poller"  # gmail.readonly only
DRAFTS_WORKLOAD_IDENTITY = "inbox-agent-drafts"  # gmail.compose only
# The provider's client secret, as `get-oauth2-credential-provider` reports it
# (clientSecretArn). The name is not the secret. Recreating the provider
# changes it.
GOOGLE_CLIENT_SECRET_NAME = "bedrock-agentcore-identity!default/oauth2/inbox-agent-google-1ed2e5b8-NDkbRv"

# OQ-44 (d): the owner's `aws login` session is this AWS-managed role (the
# new sign-up experience makes it). An Approve counts only if SQS reports this
# role as its sender; the draft tool checks the same role ID.
OWNER_ROLE_PATH = "managed/"
OWNER_ROLE_NAME = "AccountFullAccessRole"

# OQ-39 (b), OQ-41 (f): route 1, chosen 2026-10-18 after its test call
# succeeded. The classifier calls Nova Micro through the `eu.` inference
# profile from eu-north-1. The profile runs the request in one of these
# Regions (`aws bedrock get-inference-profile`, 2026-10-18), so the IAM
# grant names each Region's model, reachable only through the profile.
CLASSIFIER_MODEL_ID = "eu.amazon.nova-micro-v1:0"
CLASSIFIER_FOUNDATION_MODEL = "amazon.nova-micro-v1:0"
CLASSIFIER_PROFILE_REGIONS = ("eu-central-1", "eu-north-1", "eu-west-1", "eu-west-3")

# Chat on the Ask page (CHT-1): Nova Lite through the same `eu.` route. Its
# profile routes to the same Regions as Micro (`aws bedrock
# get-inference-profile`, 2026-10-19).
CHAT_MODEL_ID = "eu.amazon.nova-lite-v1:0"
CHAT_FOUNDATION_MODEL = "amazon.nova-lite-v1:0"

# Week 3 agent (OQ-42). Runtime names allow letters, digits and underscores.
RUNTIME_NAME = "inbox_agent"
GATEWAY_NAME = "inbox-agent"
