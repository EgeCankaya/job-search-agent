"""Week 3 infrastructure (OQ-42 a, c, e, i): the tool Lambdas, the Gateway,
the Runtime and the queue consumer."""

import pytest
from aws_cdk import App
from aws_cdk.assertions import Template

from inbox_agent.tools import TOOL_SPECS, WEEK3_TOOLS
from infra import agentcore_stack, config
from infra.app import AGENTCORE_STACK, ALWAYS_FREE_STACK, build
from infra.runtime_bundle import ENTRYPOINT, MAIN
from tests.infra.test_stacks import (
    TEST_EMAIL,
    agent_queue_id,
    as_list,
    policy_statements,
    reader_role_id,
    render_arn,
    resources,
    synth,
)


@pytest.fixture(scope="module")
def templates():
    return synth()


@pytest.fixture(scope="module")
def free(templates):
    return templates[ALWAYS_FREE_STACK]


@pytest.fixture(scope="module")
def agent(templates):
    return templates[AGENTCORE_STACK]


def one(template, type_):
    [(logical_id, resource)] = resources(template, type_).items()
    return logical_id, resource


def role_of(template, function):
    return function["Properties"]["Role"]["Fn::GetAtt"][0]


def actions(template, role_id, *, skip_logs=True):
    return {
        action
        for statement in policy_statements(template, role_id)
        for action in as_list(statement["Action"])
        if not (skip_logs and action.startswith("logs:"))
    }


# The tool Lambdas (always-free stack)


def tool_functions(free):
    return {
        fn["Properties"]["Environment"]["Variables"]["TOOL_NAME"]: fn
        for fn in resources(free, "AWS::Lambda::Function").values()
        if "TOOL_NAME" in fn["Properties"].get("Environment", {}).get("Variables", {})
    }


def test_one_function_per_week_3_tool(free):
    functions = tool_functions(free)
    assert set(functions) == set(WEEK3_TOOLS)
    for fn in functions.values():
        assert fn["Properties"]["Handler"] == "inbox_agent.tool_lambda.handler"
    assert len({role_of(free, fn) for fn in functions.values()}) == 4  # a role each


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (
            "read_email",
            {"bedrock-agentcore:GetWorkloadAccessTokenForUserId", "bedrock-agentcore:GetResourceOauth2Token", "secretsmanager:GetSecretValue"},
        ),
        ("upsert_application", {"dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem"}),
        ("list_applications", {"dynamodb:Scan", "dynamodb:GetItem"}),
        ("notify_owner", {"sns:Publish"}),
    ],
)
def test_each_tool_role_holds_only_its_tool(free, tool, expected):
    role_id = role_of(free, tool_functions(free)[tool])
    assert actions(free, role_id) == expected


def test_only_the_tracker_tools_reach_the_tracker(free):
    for tool in ("upsert_application", "list_applications"):
        role_id = role_of(free, tool_functions(free)[tool])
        tables = {
            render_arn(r)
            for s in policy_statements(free, role_id)
            if any(a.startswith("dynamodb:") for a in as_list(s["Action"]))
            for r in as_list(s["Resource"])
        }
        assert len(tables) == 1 and "ApplicationsTable" in tables.pop()


def test_tool_lambdas_stay_in_local_mode():
    # MacBook mode keeps the free stack whole; the Lambdas sit idle there.
    assert set(tool_functions(synth("local")[ALWAYS_FREE_STACK])) == set(WEEK3_TOOLS)


# The Gateway


def test_the_gateway_takes_iam_calls_and_has_no_search_tool(agent):
    _, gateway = one(agent, "AWS::BedrockAgentCore::Gateway")
    properties = gateway["Properties"]
    assert properties["AuthorizerType"] == "AWS_IAM"
    assert properties["ProtocolType"] == "MCP"
    assert "SearchType" not in properties["ProtocolConfiguration"]["Mcp"]


def test_the_gateway_serves_exactly_the_week_3_tools(agent):
    targets = resources(agent, "AWS::BedrockAgentCore::GatewayTarget").values()
    served = {}
    for target in targets:
        lambda_target = target["Properties"]["TargetConfiguration"]["Mcp"]["Lambda"]
        [tool] = lambda_target["ToolSchema"]["InlinePayload"]
        served[target["Properties"]["Name"]] = tool
    assert {name: tool["Name"] for name, tool in served.items()} == {
        agentcore_stack.target_name(name): name for name in WEEK3_TOOLS
    }
    for tool in served.values():
        spec = TOOL_SPECS[tool["Name"]]
        assert set(tool["InputSchema"]["Properties"]) == set(spec.input_schema["properties"])
        assert tool["InputSchema"].get("Required", []) == spec.input_schema.get("required", [])


def test_gateway_schemas_name_the_allowed_values():
    schema = agentcore_stack.gateway_tool(TOOL_SPECS["list_applications"])
    status = schema.input_schema.properties["status"]
    assert "open" in status.description and "rejected" in status.description


def test_the_gateway_role_may_only_invoke_the_tool_functions(agent):
    role_id = one(agent, "AWS::BedrockAgentCore::Gateway")[1]["Properties"]["RoleArn"]["Fn::GetAtt"][0]
    assert actions(agent, role_id, skip_logs=False) == {"lambda:InvokeFunction"}
    targets = {str(r) for s in policy_statements(agent, role_id) for r in as_list(s["Resource"])}
    assert len(targets) == 8  # 4 functions and their versions
    assert all(any(f"{tool.title().replace('_', '')}ToolFunction" in t for tool in WEEK3_TOOLS) for t in targets)


# The Runtime


def test_the_runtime_is_a_python_313_zip_on_the_public_network(agent):
    _, runtime = one(agent, "AWS::BedrockAgentCore::Runtime")
    properties = runtime["Properties"]
    code = properties["AgentRuntimeArtifact"]["CodeConfiguration"]
    assert code["Runtime"] == "PYTHON_3_13"
    assert code["EntryPoint"] == [ENTRYPOINT]
    assert properties["NetworkConfiguration"] == {"NetworkMode": "PUBLIC"}
    assert properties["ProtocolConfiguration"] == "HTTP"
    assert "AuthorizerConfiguration" not in properties  # IAM, the default (OQ-42 e)
    environment = properties["EnvironmentVariables"]
    assert environment["AGENT_MODE"] == "cloud"
    assert environment["CLASSIFIER_MODEL_ID"] == config.CLASSIFIER_MODEL_ID
    assert environment["CHAT_MODEL_ID"] == config.CHAT_MODEL_ID
    assert set(environment) == {"AGENT_MODE", "GATEWAY_URL", "PROCESSED_EMAILS_TABLE_NAME", "CLASSIFIER_MODEL_ID", "CHAT_MODEL_ID"}


def runtime_role_id(agent):
    return one(agent, "AWS::BedrockAgentCore::Runtime")[1]["Properties"]["RoleArn"]["Fn::GetAtt"][0]


def test_the_runtime_role_is_least_privilege(agent):
    assert actions(agent, runtime_role_id(agent), skip_logs=False) == {
        "s3:GetObject",
        "bedrock:InvokeModel",
        "bedrock-agentcore:InvokeGateway",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams",
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    }


def test_the_runtime_reaches_nova_micro_and_lite_only_through_their_profiles(agent):
    statements = [s for s in policy_statements(agent, runtime_role_id(agent)) if s["Action"] == "bedrock:InvokeModel"]
    profiles = {render_arn(r).rsplit("/", 1)[1] for s in statements if "Condition" not in s for r in as_list(s["Resource"])}
    assert profiles == {config.CLASSIFIER_MODEL_ID, config.CHAT_MODEL_ID}
    regional = [s for s in statements if "Condition" in s]
    assert len(regional) == 2
    for statement in regional:
        models = [render_arn(r) for r in as_list(statement["Resource"])]
        assert len(models) == len(config.CLASSIFIER_PROFILE_REGIONS)
        profile = render_arn(statement["Condition"]["StringEquals"]["bedrock:InferenceProfileArn"])
        assert all(m.endswith("::foundation-model/" + profile.rsplit("/", 1)[1].removeprefix("eu.")) for m in models)


def test_the_runtime_cannot_publish_alerts(agent):
    assert "sns:Publish" not in actions(agent, runtime_role_id(agent))


def test_the_runtime_log_group_is_made_with_7_days(agent):
    groups = [
        g for g in resources(agent, "AWS::Logs::LogGroup").values()
        if "LogGroupName" in g["Properties"]
    ]
    [group] = groups
    parts = group["Properties"]["LogGroupName"]["Fn::Join"][1]
    assert parts[0] == "/aws/bedrock-agentcore/runtimes/"
    assert parts[1] == {"Fn::GetAtt": ["Runtime", "AgentRuntimeId"]}
    assert parts[2] == "-DEFAULT"
    assert group["Properties"]["RetentionInDays"] == 7


def test_only_the_reader_and_the_runtime_call_bedrock(templates):
    callers = {
        role_id
        for template in templates.values()
        for role_id in resources(template, "AWS::IAM::Role")
        if any(a.startswith("bedrock:") for s in policy_statements(template, role_id) for a in as_list(s["Action"]))
    }
    assert callers == {reader_role_id(templates[ALWAYS_FREE_STACK]), runtime_role_id(templates[AGENTCORE_STACK])}


def test_the_zip_starts_the_entry_point():
    assert MAIN == "from inbox_agent.runtime import main\n\nmain()\n"


# The queue consumer


def consumer(agent):
    [(logical_id, fn)] = [
        (k, f) for k, f in resources(agent, "AWS::Lambda::Function").items() if f["Properties"]["Handler"].endswith("consumer.handler")
    ]
    return logical_id, fn


def test_the_consumer_reads_the_queue_like_the_reader_but_is_off(agent):
    _, mapping = one(agent, "AWS::Lambda::EventSourceMapping")
    properties = mapping["Properties"]
    assert properties["Enabled"] is False  # until the cutover
    assert properties["BatchSize"] == 5
    assert properties["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert properties["ScalingConfig"] == {"MaximumConcurrency": 2}


def test_the_consumer_may_only_invoke_the_runtime(agent):
    _, fn = consumer(agent)
    role_id = role_of(agent, fn)
    assert actions(agent, role_id) - {a for a in actions(agent, role_id) if a.startswith("sqs:")} == {
        "bedrock-agentcore:InvokeAgentRuntime"
    }
    [invoke] = [s for s in policy_statements(agent, role_id) if s["Action"] == "bedrock-agentcore:InvokeAgentRuntime"]
    assert as_list(invoke["Resource"])[0] == {"Fn::GetAtt": ["Runtime", "AgentRuntimeArn"]}


def test_queue_visibility_outlasts_the_consumer(free, agent):
    _, fn = consumer(agent)
    queue = resources(free, "AWS::SQS::Queue")[agent_queue_id(free)]
    assert queue["Properties"]["VisibilityTimeout"] >= 6 * fn["Properties"]["Timeout"]


def test_the_cutover_turns_the_consumer_on_and_the_reader_off(monkeypatch):
    monkeypatch.setattr(config, "AGENT_CUTOVER", True)
    assert agentcore_stack.consumer_enabled("cloud") is True
    assert agentcore_stack.consumer_enabled("local") is False
    app = App(context={"alert_email": TEST_EMAIL, "agent_mode": "cloud", "aws:cdk:bundling-stacks": []})
    stacks = build(app)
    [consumer_mapping] = resources(Template.from_stack(stacks[AGENTCORE_STACK]), "AWS::Lambda::EventSourceMapping").values()
    [reader_mapping] = resources(Template.from_stack(stacks[ALWAYS_FREE_STACK]), "AWS::Lambda::EventSourceMapping").values()
    assert consumer_mapping["Properties"]["Enabled"] is True
    assert reader_mapping["Properties"]["Enabled"] is False
