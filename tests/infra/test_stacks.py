import json
import re

import pytest
from aws_cdk import App
from aws_cdk.assertions import Template

from infra.app import AGENTCORE_STACK, ALWAYS_FREE_STACK, build

TEST_EMAIL = "alerts@example.com"
FOURTEEN_DAYS = 14 * 24 * 60 * 60

# Resource types that carry CloudFormation tags in this app.
TAGGABLE = [
    "AWS::DynamoDB::Table",
    "AWS::SQS::Queue",
    "AWS::Lambda::Function",
    "AWS::IAM::Role",
    "AWS::Logs::LogGroup",
    "AWS::SNS::Topic",
]

FORBIDDEN_PREFIXES = (
    "AWS::EC2::",  # VPC, subnets, NAT gateways, security groups, endpoints
    "AWS::ElasticLoadBalancing::",
    "AWS::ElasticLoadBalancingV2::",
    "AWS::OpenSearchServerless::",
    "AWS::SES::",  # includes Mail Manager ingress points
)


def synth(mode="cloud"):
    app = App(context={"alert_email": TEST_EMAIL, "agent_mode": mode})
    stacks = build(app)
    return {name: Template.from_stack(stack) for name, stack in stacks.items()}


@pytest.fixture(scope="module")
def templates():
    return synth()


@pytest.fixture(scope="module")
def free(templates):
    return templates[ALWAYS_FREE_STACK]


def resources(template, type_=None):
    return {
        logical_id: resource
        for logical_id, resource in template.to_json().get("Resources", {}).items()
        if type_ is None or resource["Type"] == type_
    }


def has_project_tag(tags):
    if isinstance(tags, dict):  # the budgets custom resource passes a key-value map
        return tags.get("project") == "inbox-agent"
    return {"Key": "project", "Value": "inbox-agent"} in (tags or [])


# Tags (NFR-COST-3)


@pytest.mark.parametrize("type_", TAGGABLE)
def test_every_taggable_resource_has_project_tag(free, type_):
    found = resources(free, type_)
    assert found, f"expected at least one {type_}"
    for logical_id, resource in found.items():
        assert has_project_tag(resource["Properties"].get("Tags")), logical_id


def test_budgets_get_project_tag(free):
    # Budgets are made through the API, so the tag travels as a property.
    [budgets] = resources(free, "Custom::InboxAgentBudgets").values()
    assert has_project_tag(budgets["Properties"]["Tags"])


def test_no_untagged_resource_with_tags_property(templates):
    for name, template in templates.items():
        for logical_id, resource in resources(template).items():
            props = resource.get("Properties", {})
            for key in ("Tags", "ResourceTags"):
                if key in props:
                    assert has_project_tag(props[key]), f"{name}/{logical_id}"


# Log retention (NFR-PRIV-4)


def test_every_log_group_keeps_7_days(templates):
    groups = [r for t in templates.values() for r in resources(t, "AWS::Logs::LogGroup").values()]
    assert groups
    for group in groups:
        assert group["Properties"]["RetentionInDays"] == 7


def test_every_function_uses_a_managed_log_group(free):
    # A function without LoggingConfig would create its own log group with no retention.
    for logical_id, fn in resources(free, "AWS::Lambda::Function").items():
        assert "LogGroup" in fn["Properties"].get("LoggingConfig", {}), logical_id


def test_no_log_retention_custom_resource(templates):
    for template in templates.values():
        assert not resources(template, "Custom::LogRetention")


# Queue (ING-3, NFR-REL-2)


def test_agent_queue_retention_and_dead_letter_queue(free):
    queues = resources(free, "AWS::SQS::Queue")
    assert len(queues) == 2
    main = [r for r in queues.values() if "RedrivePolicy" in r["Properties"]]
    dlq = [lid for lid, r in queues.items() if "RedrivePolicy" not in r["Properties"]]
    assert len(main) == 1 and len(dlq) == 1

    props = main[0]["Properties"]
    assert props["MessageRetentionPeriod"] == FOURTEEN_DAYS
    assert props["RedrivePolicy"]["maxReceiveCount"] == 3
    assert props["RedrivePolicy"]["deadLetterTargetArn"] == {"Fn::GetAtt": [dlq[0], "Arn"]}
    assert queues[dlq[0]]["Properties"]["MessageRetentionPeriod"] == FOURTEEN_DAYS


# DynamoDB TTL (NFR-PRIV-4)


@pytest.mark.parametrize("key", ["message_id", "approval_id"])
def test_ttl_enabled_on_log_and_pending_drafts(free, key):
    free.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": key, "KeyType": "HASH"}],
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        },
    )


def test_tracker_has_no_ttl_and_is_retained(free):
    tracker = [
        r
        for r in resources(free, "AWS::DynamoDB::Table").values()
        if r["Properties"]["KeySchema"][0]["AttributeName"] == "company_key"
    ]
    assert len(tracker) == 1
    assert "TimeToLiveSpecification" not in tracker[0]["Properties"]
    assert tracker[0]["DeletionPolicy"] == "Retain"


# Budgets (NFR-COST-1)


def test_three_budgets_with_email_alerts(free):
    [budgets] = resources(free, "Custom::InboxAgentBudgets").values()
    props = budgets["Properties"]
    assert props["Limits"] == ["1", "5", "10"]
    assert props["Email"] == TEST_EMAIL
    assert props["NamePrefix"] == "inbox-agent"


def test_no_cloudformation_budget_resources(free):
    # AWS::Budgets::Budget doesn't exist in eu-north-1; a deploy would fail.
    assert not resources(free, "AWS::Budgets::Budget")


def test_budgets_role_reaches_only_project_budgets(free):
    [statement] = [
        s
        for p in resources(free, "AWS::IAM::Policy").values()
        for s in p["Properties"]["PolicyDocument"]["Statement"]
        if any(a.startswith("budgets:") for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]]))
    ]
    assert sorted(statement["Action"]) == ["budgets:ModifyBudget", "budgets:TagResource"]
    names = [re.search(r"budget/([a-z0-9-]+)", json.dumps(arn)).group(1) for arn in statement["Resource"]]
    assert sorted(names) == ["inbox-agent-1-usd", "inbox-agent-10-usd", "inbox-agent-5-usd"]


def test_alert_email_is_required():
    with pytest.raises(ValueError, match="alert_email"):
        build(App(context={"agent_mode": "cloud"}))


# Alerts (ALR-3)


def test_alert_topic_has_one_email_subscription_to_the_owner(templates):
    subscriptions = [r for t in templates.values() for r in resources(t, "AWS::SNS::Subscription").values()]
    [subscription] = subscriptions
    [topic_id] = resources(templates[ALWAYS_FREE_STACK], "AWS::SNS::Topic")
    assert subscription["Properties"]["Protocol"] == "email"
    assert subscription["Properties"]["Endpoint"] == TEST_EMAIL
    assert subscription["Properties"]["TopicArn"] == {"Ref": topic_id}


def test_alert_topic_policy_lets_only_this_account_publish(free):
    [topic_id] = resources(free, "AWS::SNS::Topic")
    [policy] = resources(free, "AWS::SNS::TopicPolicy").values()
    assert policy["Properties"]["Topics"] == [{"Ref": topic_id}]
    statements = {s["Sid"]: s for s in policy["Properties"]["PolicyDocument"]["Statement"]}
    assert set(statements) == {
        "AllowPublishFromThisAccount",
        "DenyPublishFromOtherAccounts",
        "DenyPublishWithoutTls",
    }
    for statement in statements.values():
        assert statement["Action"] == "sns:Publish"
        assert statement["Resource"] == {"Ref": topic_id}

    allow = statements["AllowPublishFromThisAccount"]
    assert allow["Effect"] == "Allow"
    assert ":root" in json.dumps(allow["Principal"]["AWS"])
    assert {"Ref": "AWS::AccountId"} in allow["Principal"]["AWS"]["Fn::Join"][1]
    assert "Condition" not in allow

    other_accounts = statements["DenyPublishFromOtherAccounts"]
    assert other_accounts["Effect"] == "Deny"
    assert other_accounts["Condition"] == {"StringNotEquals": {"aws:PrincipalAccount": {"Ref": "AWS::AccountId"}}}

    no_tls = statements["DenyPublishWithoutTls"]
    assert no_tls["Effect"] == "Deny"
    assert no_tls["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_resource_policies_open_to_anyone_only_deny(templates):
    # Principal "*" is allowed only on a conditional Deny ("everyone else").
    for template in templates.values():
        for logical_id, policy in resources(template, "AWS::SNS::TopicPolicy").items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                if statement["Principal"] in ("*", {"AWS": "*"}):
                    assert statement["Effect"] == "Deny" and statement.get("Condition"), logical_id


# No inbound HTTP endpoint (CHT-2, UAC-CHT-2)

INBOUND_ENDPOINT_PREFIXES = (
    "AWS::ApiGateway::",  # REST APIs
    "AWS::ApiGatewayV2::",  # HTTP and WebSocket APIs
    "AWS::Lambda::Url",
)


@pytest.mark.parametrize("mode", ["cloud", "local"])
def test_no_api_gateway_or_function_url_in_any_stack(mode):
    for name, template in synth(mode).items():
        for logical_id, resource in resources(template).items():
            assert not resource["Type"].startswith(INBOUND_ENDPOINT_PREFIXES), f"{name}/{logical_id}"


# No always-on networking (NFR-COST-4)


def test_no_vpc_or_always_on_resources(templates):
    for name, template in templates.items():
        for logical_id, resource in resources(template).items():
            assert not resource["Type"].startswith(FORBIDDEN_PREFIXES), f"{name}/{logical_id}"
    for fn in resources(templates[ALWAYS_FREE_STACK], "AWS::Lambda::Function").values():
        assert "VpcConfig" not in fn["Properties"]


# Least-privilege IAM


def test_roles_have_no_managed_policies(free):
    for role in resources(free, "AWS::IAM::Role").values():
        assert not role["Properties"].get("ManagedPolicyArns")


def test_no_wildcard_actions_or_resources(free):
    for logical_id, policy in resources(free, "AWS::IAM::Policy").items():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            assert all("*" not in action for action in actions), logical_id
            assert statement["Resource"] != "*", logical_id


# Functions


def test_functions_use_python_313_arm64(free):
    for logical_id, fn in resources(free, "AWS::Lambda::Function").items():
        assert fn["Properties"]["Runtime"] == "python3.13", logical_id
        assert fn["Properties"]["Architectures"] == ["arm64"], logical_id


# Runtime mode (MODE-1, MODE-2)


def test_cloud_mode_has_both_stacks(templates):
    assert set(templates) == {ALWAYS_FREE_STACK, AGENTCORE_STACK}


def test_local_mode_turns_agentcore_stack_off():
    assert set(synth("local")) == {ALWAYS_FREE_STACK}


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="AGENT_MODE"):
        build(App(context={"alert_email": TEST_EMAIL, "agent_mode": "hybrid"}))


def test_stacks_target_home_region():
    app = App(context={"alert_email": TEST_EMAIL})
    for stack in build(app).values():
        assert stack.region == "eu-north-1"
