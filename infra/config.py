"""Settings shared by the CDK app and its tests.

Nothing personal lives here. The budget alert email comes from CDK context or
the environment, and every secret lives in SSM Parameter Store.
"""

import os

from constructs import Construct

PROJECT_TAG = ("project", "inbox-agent")
# The account uses AWS's new sign-up experience, which pins every project to
# a home Region picked from the contact country: Turkey -> Stockholm (OQ-39 f).
REGION = "eu-north-1"

AGENT_MODES = ("cloud", "local")

BUDGET_LIMITS_USD = (1, 5, 10)
BUDGET_NAME_PREFIX = "inbox-agent"

# SecureString parameters the webhook reads (NFR-SEC-3, ALR-3). CloudFormation
# cannot create SecureStrings, so CDK only references these names; their values
# are put in place by hand with `aws ssm put-parameter --type SecureString`.
SSM_PREFIX = "/inbox-agent"
SSM_META_VERIFY_TOKEN = f"{SSM_PREFIX}/meta/verify-token"
SSM_META_ACCESS_TOKEN = f"{SSM_PREFIX}/meta/access-token"
SSM_META_APP_SECRET = f"{SSM_PREFIX}/meta/app-secret"
SSM_META_PHONE_NUMBER_ID = f"{SSM_PREFIX}/meta/phone-number-id"
SSM_OWNER_WHATSAPP_NUMBER = f"{SSM_PREFIX}/owner/whatsapp-number"

WEBHOOK_PARAMETERS = (
    SSM_META_VERIFY_TOKEN,
    SSM_META_ACCESS_TOKEN,
    SSM_META_APP_SECRET,
    SSM_META_PHONE_NUMBER_ID,
    SSM_OWNER_WHATSAPP_NUMBER,
)


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
    """Address for AWS Budgets alerts. Supplied at synth time, never committed."""
    email = _setting(scope, "alert_email", "ALERT_EMAIL")
    if not email or "@" not in email:
        raise ValueError(
            "Set the budget alert address: `cdk synth -c alert_email=you@example.com` "
            "or the ALERT_EMAIL environment variable."
        )
    return email
