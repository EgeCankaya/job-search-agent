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


@pytest.mark.parametrize("name", ["deploy.yml", "check.yml", "tier2.yml"])
def test_no_secrets_and_read_only_tokens(name):
    text = (WORKFLOWS / name).read_text()
    assert not re.search(r"\$\{\{\s*secrets", text)
    assert not re.search(r"aws-access-key-id|aws-secret-access-key|AWS_SECRET|id-token", text)
    assert load(name)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("name", ["deploy.yml", "check.yml", "tier2.yml"])
def test_every_action_is_pinned_to_a_commit(name):
    for step in steps(load(name)):
        if "uses" in step:
            assert SHA.match(step["uses"]), step["uses"]
            assert step.get("with", {}).get("persist-credentials") is False or "checkout" not in step["uses"]


def test_the_address_and_account_are_masked():
    text = (WORKFLOWS / "deploy.yml").read_text()
    assert 'echo "::add-mask::$account"' in text
    assert 'echo "::add-mask::$ALERT_EMAIL"' in text


def test_tier1_runs_in_the_check_after_the_unit_tests_without_aws():
    """OQ-47 (b): Tier 1 gates every pull request and every deploy, on GitHub's runner."""
    runs = [step.get("run", "") for step in steps(load("check.yml"))]
    tier1 = next(i for i, run in enumerate(runs) if "eval/tier1.py" in run)
    assert tier1 > next(i for i, run in enumerate(runs) if "pytest" in run)
    assert not any("aws " in run or "ollama" in run.lower() or "record.py" in run for run in runs)


def test_tier2_runs_on_the_mac_never_on_pull_requests():
    """OQ-47 (c): after a deploy, weekly and by hand; scores only leave the Mac."""
    tier2 = load("tier2.yml")
    assert set(tier2["on"]) == {"workflow_call", "workflow_dispatch", "schedule"}
    assert tier2["on"]["schedule"] == [{"cron": "0 7 * * 1"}]
    assert "pull_request" not in (WORKFLOWS / "tier2.yml").read_text().replace("pull_request trigger", "")
    job = tier2["jobs"]["tier2"]
    assert job["runs-on"] == ["self-hosted", "macOS"] and job["timeout-minutes"] == 60
    runs = [step.get("run", "") for step in job["steps"]]
    assert "aws sts get-caller-identity" in runs[1]
    assert any("run_labeled.py --split test --pace 12" in run for run in runs)
    assert any("run_injection.py --mode cloud" in run for run in runs)
    assert any("run_chat.py --pace 12 --evaluate" in run for run in runs)
    [upload] = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload["with"]["path"] == "scores/"


def test_only_main_replaces_the_saved_main_run():
    [save] = [s for s in steps(load("tier2.yml")) if "main-latest.json" in s.get("run", "") and s["run"].startswith("cp")]
    assert save["if"].startswith("${{ github.ref == 'refs/heads/main'")


def test_deploy_calls_tier2_after_the_deploy_when_code_changed():
    deploy = load("deploy.yml")
    job = deploy["jobs"]["tier2"]
    assert job["uses"] == "./.github/workflows/tier2.yml"
    assert set(job["needs"]) == {"deploy", "changes"}
    assert "refs/heads/main" in job["if"] and "needs.changes.outputs.eval == 'true'" in job["if"]
    [diff] = [s for s in deploy["jobs"]["changes"]["steps"] if s.get("id") == "diff"]
    assert "^(src|eval|policies)/" in diff["run"]
