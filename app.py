#!/usr/bin/env python3
import sys
from pathlib import Path

# The CDK reads the tool schemas from the runtime package (src/inbox_agent).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from aws_cdk import App  # noqa: E402

from infra.app import build  # noqa: E402

app = App()
build(app)
app.synth()
