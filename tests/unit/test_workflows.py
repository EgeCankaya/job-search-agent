"""OPS-1 (OQ-43) and the runner safety rule (§12): what the GitHub Actions
workflows may and may not do."""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def load(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML reads a bare `on` as True
    return data


def steps(workflow):
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_deploy_runs_only_for_main_and_never_on_pull_requests():
    deploy = load("deploy.yml")
    assert set(deploy["on"]) == {"push", "workflow_dispatch"}
    assert deploy["on"]["push"] == {"branches": ["main"]}
    assert "pull_request" not in (WORKFLOWS / "deploy.yml").read_text().replace("pull_request trigger", "")
    job = deploy["jobs"]["deploy"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["needs"] == "check"


def test_deploy_runs_on_the_self_hosted_runner_and_check_on_github():
    deploy, check = load("deploy.yml"), load("check.yml")
    assert deploy["jobs"]["deploy"]["runs-on"] == ["self-hosted", "macOS"]
    assert deploy["jobs"]["check"]["uses"] == "./.github/workflows/check.yml"
    assert check["jobs"]["check"]["runs-on"].startswith("ubuntu-")
    assert set(check["on"]) == {"pull_request", "workflow_call"}


def test_deploys_one_at_a_time_and_only_the_app_stacks():
    deploy = load("deploy.yml")
    assert deploy["concurrency"] == {"group": "deploy", "cancel-in-progress": False}
    [command] = [s["run"] for s in steps(deploy) if "cdk deploy" in s.get("run", "")]
    assert "cdk deploy InboxAgentAlwaysFree InboxAgentAgentCore " in command
    assert "--all" not in command


@pytest.mark.parametrize("name", ["deploy.yml", "check.yml"])
def test_no_secrets_and_read_only_tokens(name):
    text = (WORKFLOWS / name).read_text()
    assert not re.search(r"\$\{\{\s*secrets", text)
    assert not re.search(r"aws-access-key-id|aws-secret-access-key|AWS_SECRET|id-token", text)
    assert load(name)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("name", ["deploy.yml", "check.yml"])
def test_every_action_is_pinned_to_a_commit(name):
    for step in steps(load(name)):
        if "uses" in step:
            assert SHA.match(step["uses"]), step["uses"]
            assert step.get("with", {}).get("persist-credentials") is False or "checkout" not in step["uses"]


def test_the_address_and_account_are_masked():
    text = (WORKFLOWS / "deploy.yml").read_text()
    assert 'echo "::add-mask::$account"' in text
    assert 'echo "::add-mask::$ALERT_EMAIL"' in text
