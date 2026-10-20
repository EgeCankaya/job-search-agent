"""The ZIPs that carry dependencies, built by uv for Linux arm64 (glibc 2.28
wheels: numpy publishes no older ones) and Python 3.13, on the machine that
runs `cdk synth`, so no Docker is needed. Wheels only: a package without an
arm64 wheel fails the synth, not the deploy.

- The AgentCore Runtime (OQ-42 i): the `inbox_agent` package, the `agent`
  dependency group, and a main.py that starts the entry point.
- The approval workflow (OQ-44 a, n): the package and the `approval` group
  (the durable-execution SDK, and the MCP client for the Gateway).

Tests skip bundling (the `aws:cdk:bundling-stacks` context), so they never
download anything.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import jsii
from aws_cdk import AssetHashType, BundlingOptions, DockerImage, ILocalBundling

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "inbox_agent"
ENTRYPOINT = "main.py"
MAIN = "from inbox_agent.runtime import main\n\nmain()\n"


@jsii.implements(ILocalBundling)
class UvBundling:
    def __init__(self, group: str = "agent", main: bool = True) -> None:
        self.group = group
        self.main = main

    def try_bundle(self, output_dir: str, *, image, **kwargs) -> bool:
        with tempfile.TemporaryDirectory() as scratch:
            requirements = Path(scratch) / "requirements.txt"
            # Written by uv itself, colour off: the CDK forces colour, and
            # escape codes in the file break the install.
            subprocess.run(
                [
                    "uv", "export", "--frozen", "--only-group", self.group, "--no-hashes", "--no-emit-project",
                    "--color", "never", "--output-file", str(requirements),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            installed = subprocess.run(
                [
                    "uv", "pip", "install",
                    "--target", output_dir,
                    "--python-platform", "aarch64-manylinux_2_28",
                    "--python-version", "3.13",
                    "--only-binary", ":all:",
                    "--requirement", str(requirements),
                    "--color", "never",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if installed.returncode != 0:
                raise RuntimeError(f"uv pip install failed:\n{installed.stderr[-2000:]}")
        shutil.copytree(PACKAGE, Path(output_dir) / PACKAGE.name, ignore=shutil.ignore_patterns("__pycache__"))
        if self.main:
            (Path(output_dir) / ENTRYPOINT).write_text(MAIN)
        return True


def runtime_bundling() -> dict:
    """Asset options for the Runtime code. The hash is the output's, so a
    changed lock file makes a new ZIP even when the source didn't change."""
    return {
        "path": str(PACKAGE),
        "asset_hash_type": AssetHashType.OUTPUT,
        "bundling": BundlingOptions(image=DockerImage.from_registry("unused-local-bundling-only"), local=UvBundling()),
    }


def approval_bundling() -> dict:
    """Code options for the approval workflow's Lambda."""
    return {
        "asset_hash_type": AssetHashType.OUTPUT,
        "bundling": BundlingOptions(
            image=DockerImage.from_registry("unused-local-bundling-only"),
            local=UvBundling(group="approval", main=False),
            # The CDK caches a bundle by its options, not by the local
            # bundler; without this the Runtime's bundle would be reused.
            environment={"UV_GROUP": "approval"},
        ),
    }
