"""AGENT_MODE=cloud|local (MODE-1): the one setting that picks the runtime,
model, tools and memory backend. The CDK reads the same name
(infra/config.py)."""

import os

AGENT_MODE_ENV = "AGENT_MODE"
AGENT_MODES = ("cloud", "local")


def agent_mode(environ=os.environ) -> str:
    """Defaults to cloud, like the CDK."""
    mode = (environ.get(AGENT_MODE_ENV) or "cloud").strip().lower()
    if mode not in AGENT_MODES:
        raise ValueError(f"{AGENT_MODE_ENV} must be one of {AGENT_MODES}, got {mode!r}")
    return mode
