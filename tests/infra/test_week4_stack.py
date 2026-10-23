"""Week 4 infrastructure (OQ-44 a, b): the approval workflow's durable
function, its role, and the one named wildcard."""

import json

import pytest

from infra import config
from infra.app import AGENTCORE_STACK, ALWAYS_FREE_STACK
from tests.infra.test_stacks import as_list, policy_statements, render_arn, resources, synth


@pytest.fixture(scope="module")
def templates():
    return synth()


@pytest.fixture(scope="module")
def agent(templates):
    return templates[AGENTCORE_STACK]


def approval_function(agent):
    [(logical_id, fn)] = [
        (lid, fn) for lid, fn in resources(agent, "AWS::Lambda::Function").items()
        if fn["Properties"].get("FunctionName") == config.APPROVAL_FUNCTION_NAME
    ]  # fmt: skip
    return logical_id, fn


def approval_role_id(agent):
    return approval_function(agent)[1]["Properties"]["Role"]["Fn::GetAtt"][0]


def test_the_workflow_is_durable_for_8_days_and_published_as_a_version(agent):
    logical_id, fn = approval_function(agent)
    properties = fn["Properties"]
    assert properties["Handler"] == "inbox_agent.approval_workflow.handler"
    assert properties["DurableConfig"] == {"ExecutionTimeout": 8 * 24 * 3600, "RetentionPeriodInDays": 14}
    assert properties["Timeout"] == 60
    assert set(properties["Environment"]["Variables"]) == {"GATEWAY_URL", "PENDING_DRAFTS_TABLE_NAME", "TRACKER_TABLE_NAME"}
    versions = resources(agent, "AWS::Lambda::Version").values()
    assert [v["Properties"]["FunctionName"] for v in versions] == [{"Ref": logical_id}]


def test_a_deploy_never_deletes_an_old_workflow_version(agent):
    # A wait runs on the version it started on, for up to 7 days. On
    # 2026-10-21 a deploy's cleanup tried to delete version 2 while a draft
    # waited on it; Lambda refused and the stack hung in cleanup.
    [version] = resources(agent, "AWS::Lambda::Version").values()
    assert version["DeletionPolicy"] == "Retain"
    assert version["UpdateReplacePolicy"] == "Retain"


def test_the_workflow_role_holds_only_what_the_workflow_needs(agent):
    statements = policy_statements(agent, approval_role_id(agent))
    actions = {a for s in statements for a in as_list(s["Action"]) if not a.startswith("logs:")}
    assert actions == {
        "lambda:CheckpointDurableExecution",
        "lambda:GetDurableExecutionState",
        "bedrock-agentcore:InvokeGateway",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    }
    [durable] = [s for s in statements if "lambda:CheckpointDurableExecution" in as_list(s["Action"])]
    assert render_arn(durable["Resource"]).endswith(f":function:{config.APPROVAL_FUNCTION_NAME}:*")
    [puts] = [s for s in statements if "dynamodb:PutItem" in as_list(s["Action"])]
    assert "PendingDraftsTable" in json.dumps(puts["Resource"])


def test_the_workflow_may_update_only_the_follow_up_map(agent):
    [update] = [s for s in policy_statements(agent, approval_role_id(agent)) if "dynamodb:UpdateItem" in as_list(s["Action"])]
    assert "ApplicationsTable" in json.dumps(update["Resource"])
    assert update["Condition"] == {
        "ForAllValues:StringEquals": {"dynamodb:Attributes": ["company_key", "role_key", "follow_up"]},
        "StringEqualsIfExists": {"dynamodb:ReturnValues": ["NONE"]},
    }


def test_the_only_wildcards_are_the_named_ones(templates):
    """OQ-44 (b): the workflow's durable executions; plus, since week 3, the
    Gateway's invoke grant on each tool function and its versions, which
    CDK writes as <function ARN>:*. Anything else is refused."""
    durable_actions = {
        "lambda:CheckpointDurableExecution",
        "lambda:GetDurableExecutionState",
        "lambda:SendDurableExecutionCallbackSuccess",
        "lambda:SendDurableExecutionCallbackFailure",
    }
    for name, template in templates.items():
        for logical_id, policy in resources(template, "AWS::IAM::Policy").items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                for resource in as_list(statement["Resource"]):
                    text = json.dumps(resource)
                    if "*" not in text:
                        continue
                    actions = set(as_list(statement["Action"]))
                    if text.endswith(f':function:{config.APPROVAL_FUNCTION_NAME}:*"]]}}'):
                        assert actions <= durable_actions, (name, logical_id)
                    else:
                        assert actions == {"lambda:InvokeFunction"}, (name, logical_id, text)
                        assert logical_id.startswith("GatewayRole") and text.endswith(':*"]]}'), (name, logical_id)


def test_local_mode_has_no_approval_workflow():
    local = synth("local")
    assert AGENTCORE_STACK not in local
    assert not any(
        fn["Properties"].get("FunctionName") == config.APPROVAL_FUNCTION_NAME
        for fn in resources(local[ALWAYS_FREE_STACK], "AWS::Lambda::Function").values()
    )


def test_the_daily_check_goes_to_the_queue_at_9_istanbul_and_is_on(templates):
    free = templates[ALWAYS_FREE_STACK]
    [schedule] = [s for s in resources(free, "AWS::Scheduler::Schedule").values() if "stale" in s["Properties"]["Description"].lower()]
    properties = schedule["Properties"]
    assert properties["ScheduleExpression"] == "cron(0 9 * * ? *)"
    assert properties["ScheduleExpressionTimezone"] == "Europe/Istanbul"
    assert properties["State"] == "ENABLED"  # the owner's switch, STALE_CHECK_ON (on since 2026-10-21)
    assert json.loads(properties["Target"]["Input"]) == {"type": "stale_check"}
    assert "AgentQueue" in json.dumps(properties["Target"]["Arn"])


def test_the_daily_check_exists_in_local_mode_too():
    free = synth("local")[ALWAYS_FREE_STACK]
    assert any("stale" in s["Properties"]["Description"].lower() for s in resources(free, "AWS::Scheduler::Schedule").values())


def test_the_check_schedule_may_only_send_to_the_queue(templates):
    free = templates[ALWAYS_FREE_STACK]
    [schedule] = [s for s in resources(free, "AWS::Scheduler::Schedule").values() if "stale" in s["Properties"]["Description"].lower()]
    role_id = schedule["Properties"]["Target"]["RoleArn"]["Fn::GetAtt"][0]
    [statement] = policy_statements(free, role_id)
    assert statement["Action"] == "sqs:SendMessage" and "AgentQueue" in json.dumps(statement["Resource"])


def test_memory_events_expire_after_30_days_with_two_built_in_strategies(agent):
    [memory] = resources(agent, "AWS::BedrockAgentCore::Memory").values()
    properties = memory["Properties"]
    assert properties["EventExpiryDuration"] == 30  # MEM-2 acceptance, NFR-PRIV-4
    assert "MemoryExecutionRoleArn" not in properties  # built-in strategies, no role (OQ-44 h)
    strategies = properties["MemoryStrategies"]
    assert strategies == [
        {"UserPreferenceMemoryStrategy": {"Name": "preferences", "Namespaces": ["/preferences/{actorId}"]}},
        {"SummaryMemoryStrategy": {"Name": "company_summaries", "Namespaces": ["/summaries/{actorId}/{sessionId}"]}},
    ]


def test_only_the_runtime_reaches_memory_and_never_deletes(templates):
    for name, template in templates.items():
        for logical_id, policy in resources(template, "AWS::IAM::Policy").items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                memory_actions = {
                    a for a in as_list(statement["Action"])
                    if a.startswith("bedrock-agentcore:") and ("Memory" in a or a.endswith(("Event", "Events")))
                }
                if memory_actions:
                    assert logical_id.startswith("RuntimeRole"), (name, logical_id)
                    assert memory_actions == {
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                    }
                    assert as_list(statement["Resource"]) == [{"Fn::GetAtt": ["Memory", "MemoryArn"]}]


def test_the_only_question_mark_pattern_is_cdks_gateway_name(templates):
    """CDK grants the Gateway role AuthorizeAction on its own Gateway by a
    name pattern, because the Gateway's ID doesn't exist before the role."""
    seen = []
    for name, template in templates.items():
        for logical_id, policy in resources(template, "AWS::IAM::Policy").items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                for resource in as_list(statement["Resource"]):
                    if "?" in json.dumps(resource):
                        seen.append(logical_id)
                        assert logical_id.startswith("GatewayRole")
                        assert set(as_list(statement["Action"])) == {
                            "bedrock-agentcore:AuthorizeAction",
                            "bedrock-agentcore:PartiallyAuthorizeActions",
                        }
                        assert render_arn(resource).endswith(":gateway/inbox-agent-??????????")
    assert len(seen) == 1


def test_the_gateway_enforces_the_week_4_policies(agent):
    [gateway] = resources(agent, "AWS::BedrockAgentCore::Gateway").values()
    assert gateway["Properties"]["PolicyEngineConfiguration"]["Mode"] == "ENFORCE"
    texts = {
        lid: "".join(p if isinstance(p, str) else "<token>" for p in r["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Join"][1])
        for lid, r in resources(agent, "AWS::BedrockAgentCore::Policy").items()
    }
    # Week 5 adds the two forbids (OQ-45 a, b).
    assert len(texts) == 5
    descriptions = sorted(r["Properties"]["Description"] for r in resources(agent, "AWS::BedrockAgentCore::Policy").values())
    assert descriptions == [
        "OQ-44 (j): AgentTools",
        "OQ-44 (j): ApprovalDraft",
        "OQ-44 (j): OnlyTheWorkflowDrafts",
        "OQ-45 (a): NeverSend",
        "OQ-45 (b): NoRecipientKeys",
    ]
    forbids = [t for t in texts.values() if t.startswith("forbid")]
    assert len(forbids) == 3
    assert any("create-gmail-draft___create_gmail_draft" in t and "unless { principal ==" in t for t in forbids)


def test_only_the_two_week_5_forbids_may_ever_ignore_findings(agent):
    # OQ-45 (c'): NeverSend only, for its "Any Future Tools" finding.
    from infra import agentcore_stack

    assert agentcore_stack.IGNORE_FINDINGS <= {"NeverSend", "NoRecipientKeys"}
    assert agentcore_stack.IGNORE_FINDINGS == frozenset({"NeverSend"})
    assert agentcore_stack.HELD_BACK == frozenset()
    modes = {r["Properties"]["Description"].split(": ")[1]: r["Properties"]["ValidationMode"] for r in resources(agent, "AWS::BedrockAgentCore::Policy").values()}
    assert {name for name, mode in modes.items() if mode != "FAIL_ON_ANY_FINDINGS"} == set(agentcore_stack.IGNORE_FINDINGS)


def test_the_committed_config_keeps_the_probe_off(agent):
    from infra import agentcore_stack

    assert agentcore_stack.POLICY_PROBE_ON is False
    [runtime] = resources(agent, "AWS::BedrockAgentCore::Runtime").values()
    assert "POLICY_PROBE" not in runtime["Properties"]["EnvironmentVariables"]


def test_every_policy_waits_for_every_gateway_target(agent):
    targets = set(resources(agent, "AWS::BedrockAgentCore::GatewayTarget"))
    assert len(targets) == 6
    for logical_id, policy in resources(agent, "AWS::BedrockAgentCore::Policy").items():
        assert targets <= set(policy.get("DependsOn", [])), logical_id
