#!/usr/bin/env python3
from aws_cdk import App

from infra.app import build

app = App()
build(app)
app.synth()
