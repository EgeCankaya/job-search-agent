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
    "AWS::Scheduler::ScheduleGroup",
]

FORBIDDEN_PREFIXES = (
    "AWS::EC2::",  # VPC, subnets, NAT gateways, security groups, endpoints
    "AWS::ElasticLoadBalancing::",
    "AWS::ElasticLoadBalancingV2::",
    "AWS::OpenSearchServerless::",
    "AWS::SES::",  # includes Mail Manager ingress points
)


def synth(mode="cloud"):
    # No bundling: the Runtime ZIP would download its dependencies.
    app = App(context={"alert_email": TEST_EMAIL, "agent_mode": mode, "aws:cdk:bundling-stacks": []})
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


def test_every_function_uses_a_managed_log_group(templates):
    # A function without LoggingConfig would create its own log group with no retention.
    for template in templates.values():
        for logical_id, fn in resources(template, "AWS::Lambda::Function").items():
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


@pytest.mark.parametrize("bad", ["@example.com", "alerts@", "alerts@example", "a b@example.com", "alerts"])
def test_a_partial_alert_email_fails_at_synth(bad):
    # A shell typo like ALERT_EMAIL="$name@example.com" leaves "@example.com".
    with pytest.raises(ValueError, match="whole alert address"):
        build(App(context={"alert_email": bad, "agent_mode": "cloud"}))


# Alerts (ALR-3)


def test_alert_topic_has_one_email_subscription_to_the_owner(templates):
    subscriptions = [r for t in templates.values() for r in resources(t, "AWS::SNS::Subscription").values()]
    [subscription] = subscriptions
    [topic_id] = resources(templates[ALWAYS_FREE_STACK], "AWS::SNS::Topic")
    assert subscription["Properties"]["Protocol"] == "email"
    assert subscription["Properties"]["Endpoint"] == TEST_EMAIL
    assert subscription["Properties"]["TopicArn"] == {"Ref": topic_id}


def topic_policy(template):
    [topic_id] = resources(template, "AWS::SNS::Topic")
    [policy] = resources(template, "AWS::SNS::TopicPolicy").values()
    assert policy["Properties"]["Topics"] == [{"Ref": topic_id}]
    return topic_id, {s["Sid"]: s for s in policy["Properties"]["PolicyDocument"]["Statement"]}


def dead_letter_alarm_id(template):
    [alarm_id] = resources(template, "AWS::CloudWatch::Alarm")
    return alarm_id


def notify_owner_role_id(template):
    [role_id] = [r for r in resources(template, "AWS::IAM::Role") if r.startswith("NotifyOwnerToolRole")]
    return role_id


@pytest.mark.parametrize("cutover", [False, True])
def test_alert_topic_policy_lets_only_the_publisher_and_the_alarm_publish(monkeypatch, cutover):
    # ALR-3: the reader until the cutover, then the notify_owner tool
    # (OQ-41 e, OQ-42 f); the dead-letter alarm in both (OQ-42 g).
    from infra import config

    monkeypatch.setattr(config, "AGENT_CUTOVER", cutover)
    free = synth()[ALWAYS_FREE_STACK]
    topic_id, statements = topic_policy(free)
    assert set(statements) == {
        "AllowPublishFromThePublisher",
        "AllowPublishFromTheDeadLetterAlarm",
        "DenyPublishFromOtherPrincipals",
        "DenyPublishWithoutTls",
    }
    for statement in statements.values():
        assert statement["Action"] == "sns:Publish"
        assert statement["Resource"] == {"Ref": topic_id}

    publisher_id = notify_owner_role_id(free) if cutover else reader_role_id(free)
    publisher = {"Fn::GetAtt": [publisher_id, "Arn"]}
    alarm = {"Fn::GetAtt": [dead_letter_alarm_id(free), "Arn"]}

    allow = statements["AllowPublishFromThePublisher"]
    assert allow["Effect"] == "Allow"
    assert allow["Principal"] == {"AWS": publisher}
    assert "Condition" not in allow

    from_alarm = statements["AllowPublishFromTheDeadLetterAlarm"]
    assert from_alarm["Effect"] == "Allow"
    assert from_alarm["Principal"] == {"Service": "cloudwatch.amazonaws.com"}
    assert from_alarm["Condition"] == {
        "ArnEquals": {"aws:SourceArn": alarm},
        "StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}},
    }

    others = statements["DenyPublishFromOtherPrincipals"]
    assert others["Effect"] == "Deny"
    assert others["Principal"] == {"AWS": "*"}
    assert others["Condition"] == {
        "ArnNotEquals": {"aws:PrincipalArn": publisher},
        "StringNotEquals": {"aws:SourceArn": alarm},
    }

    no_tls = statements["DenyPublishWithoutTls"]
    assert no_tls["Effect"] == "Deny"
    assert no_tls["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_the_dead_letter_alarm_emails_through_the_alert_topic(free):
    # ING-3: 1 or more dead letters, within one 5-minute period.
    [topic_id] = resources(free, "AWS::SNS::Topic")
    [dlq_id] = [k for k, q in resources(free, "AWS::SQS::Queue").items() if "RedrivePolicy" not in q["Properties"]]
    alarm = resources(free, "AWS::CloudWatch::Alarm")[dead_letter_alarm_id(free)]["Properties"]
    assert alarm["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert alarm["Namespace"] == "AWS/SQS"
    assert alarm["Dimensions"] == [{"Name": "QueueName", "Value": {"Fn::GetAtt": [dlq_id, "QueueName"]}}]
    assert alarm["Statistic"] == "Maximum"
    assert alarm["Period"] == 300
    assert alarm["Threshold"] == 1
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert alarm["EvaluationPeriods"] == 1
    assert alarm["TreatMissingData"] == "notBreaching"
    assert alarm["AlarmActions"] == [{"Ref": topic_id}]
    assert "OKActions" not in alarm and "InsufficientDataActions" not in alarm


def test_no_other_role_may_publish(templates):
    # The reader until the cutover, and the notify_owner tool (OQ-42 f).
    publishers = {
        role_id
        for template in templates.values()
        for role_id in resources(template, "AWS::IAM::Role")
        if any("sns:Publish" in as_list(s["Action"]) for s in policy_statements(template, role_id))
    }
    reader = reader_role_id(templates[ALWAYS_FREE_STACK])
    assert reader in publishers
    [tool] = publishers - {reader}
    assert tool.startswith("NotifyOwnerToolRole")


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


def test_roles_have_no_managed_policies(templates):
    for template in templates.values():
        for role in resources(template, "AWS::IAM::Role").values():
            assert not role["Properties"].get("ManagedPolicyArns")


def test_no_wildcard_actions_or_resources(templates):
    for template in templates.values():
        for logical_id, policy in resources(template, "AWS::IAM::Policy").items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
                assert all("*" not in action for action in actions), logical_id
                assert statement["Resource"] != "*", logical_id
                assert "*" not in as_list(statement["Resource"]), logical_id


# Functions


def test_functions_use_python_313_arm64(templates):
    for template in templates.values():
        for logical_id, fn in resources(template, "AWS::Lambda::Function").items():
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
    app = App(context={"alert_email": TEST_EMAIL, "aws:cdk:bundling-stacks": []})
    for stack in build(app).values():
        assert stack.region == "eu-north-1"


# Tracker import (TRK-3)


def test_tracker_table_name_is_a_stack_output(free):
    [tracker_id] = [
        lid
        for lid, r in resources(free, "AWS::DynamoDB::Table").items()
        if r["Properties"]["KeySchema"][0]["AttributeName"] == "company_key"
    ]
    assert free.to_json()["Outputs"]["ApplicationsTableName"]["Value"] == {"Ref": tracker_id}


# Gmail poll (ING-1, ING-2, ING-3, MODE-2)


def poll_function(template):
    [(logical_id, fn)] = [
        (lid, r)
        for lid, r in resources(template, "AWS::Lambda::Function").items()
        if r["Properties"]["Handler"] == "inbox_agent.poll.handler"
    ]
    return logical_id, fn


def logical_id_by_key(template, key):
    [logical_id] = [
        lid
        for lid, r in resources(template, "AWS::DynamoDB::Table").items()
        if r["Properties"]["KeySchema"][0]["AttributeName"] == key
    ]
    return logical_id


def test_poll_function_gets_tables_and_queue_from_the_stack(free):
    _, fn = poll_function(free)
    queue_id = [lid for lid, r in resources(free, "AWS::SQS::Queue").items() if "RedrivePolicy" in r["Properties"]][0]
    assert fn["Properties"]["Environment"]["Variables"] == {
        "TRACKER_TABLE_NAME": {"Ref": logical_id_by_key(free, "company_key")},
        "PROCESSED_EMAILS_TABLE_NAME": {"Ref": logical_id_by_key(free, "message_id")},
        "AGENT_QUEUE_URL": {"Ref": queue_id},
    }


def test_failed_polls_are_not_retried(free):
    poll_id, _ = poll_function(free)
    [config] = resources(free, "AWS::Lambda::EventInvokeConfig").values()
    assert config["Properties"]["FunctionName"] == {"Ref": poll_id}
    assert config["Properties"]["MaximumRetryAttempts"] == 0


def policy_statements(template, role_id):
    return [
        s
        for p in resources(template, "AWS::IAM::Policy").values()
        if {"Ref": role_id} in p["Properties"]["Roles"]
        for s in p["Properties"]["PolicyDocument"]["Statement"]
    ]


def as_list(value):
    return value if isinstance(value, list) else [value]


def render_arn(value):
    """An ARN built with Fn::Join, as text with ${Partition} and ${AccountId} placeholders."""
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return "${" + value["Ref"].split("::")[-1] + "}"
    if "Fn::GetAtt" in value:
        return "<" + value["Fn::GetAtt"][0] + ">"
    separator, parts = value["Fn::Join"]
    return separator.join(render_arn(part) for part in parts)


def test_poll_role_is_least_privilege(free):
    _, fn = poll_function(free)
    role_id = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    granted = {}
    for statement in policy_statements(free, role_id):
        assert statement["Effect"] == "Allow"
        for action in as_list(statement["Action"]):
            granted[action] = sorted(render_arn(r) for r in as_list(statement["Resource"]))

    tracker = f"<{logical_id_by_key(free, 'company_key')}>"
    processed = f"<{logical_id_by_key(free, 'message_id')}>"
    queue_id = [lid for lid, r in resources(free, "AWS::SQS::Queue").items() if "RedrivePolicy" in r["Properties"]][0]
    log_group = f"<{fn['Properties']['LoggingConfig']['LogGroup']['Ref']}>"
    agentcore = "arn:${Partition}:bedrock-agentcore:eu-north-1:${AccountId}"
    directory = f"{agentcore}:workload-identity-directory/default"
    workload = f"{directory}/workload-identity/inbox-agent-poller"
    vault = f"{agentcore}:token-vault/default"
    provider = f"{vault}/oauth2credentialprovider/inbox-agent-google"
    secret = (
        "arn:${Partition}:secretsmanager:eu-north-1:${AccountId}:secret:"
        "bedrock-agentcore-identity!default/oauth2/inbox-agent-google-1ed2e5b8-NDkbRv"
    )
    assert granted == {
        "dynamodb:Scan": [tracker],
        "dynamodb:GetItem": [processed],
        "dynamodb:PutItem": [processed],
        "sqs:SendMessage": [f"<{queue_id}>"],
        "logs:CreateLogStream": [log_group],
        "logs:PutLogEvents": [log_group],
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId": sorted([directory, workload]),
        "bedrock-agentcore:GetResourceOauth2Token": sorted([directory, workload, vault, provider]),
        "secretsmanager:GetSecretValue": [secret],
    }


def test_poll_role_cannot_reach_the_drafts_identity(free):
    # ING-4: the compose grant belongs to inbox-agent-drafts; the poller never gets it.
    _, fn = poll_function(free)
    role_id = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    text = json.dumps([render_arn(r) for s in policy_statements(free, role_id) for r in as_list(s["Resource"])])
    assert "inbox-agent-drafts" not in text


def test_identity_names_match_the_runtime_settings():
    from inbox_agent import settings

    from infra import config

    assert config.GOOGLE_CREDENTIAL_PROVIDER == settings.GOOGLE_CREDENTIAL_PROVIDER
    assert config.POLLER_WORKLOAD_IDENTITY == settings.POLLER_WORKLOAD_IDENTITY
    assert config.GOOGLE_CLIENT_SECRET_NAME.startswith(
        f"bedrock-agentcore-identity!default/oauth2/{config.GOOGLE_CREDENTIAL_PROVIDER}-"
    )


def schedule(template):
    [schedule] = resources(template, "AWS::Scheduler::Schedule").values()
    return schedule["Properties"]


def test_poll_runs_every_30_minutes(free):
    poll_id, _ = poll_function(free)
    props = schedule(free)
    assert props["ScheduleExpression"] == "rate(30 minutes)"
    assert props["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert props["Target"]["Arn"] == {"Fn::GetAtt": [poll_id, "Arn"]}
    assert props["Target"]["RetryPolicy"] == {"MaximumEventAgeInSeconds": 1800, "MaximumRetryAttempts": 2}


@pytest.mark.parametrize(("mode", "state"), [("cloud", "ENABLED"), ("local", "DISABLED")])
def test_the_poll_schedule_runs_only_in_cloud_mode(mode, state):
    # OQ-41 (c): in MacBook mode the Mac worker polls, with the Keychain token.
    assert schedule(synth(mode)[ALWAYS_FREE_STACK])["State"] == state


def test_the_poll_schedule_is_off_while_the_owner_switch_is_off(monkeypatch):
    from infra import always_free_stack

    monkeypatch.setattr(always_free_stack, "POLL_SCHEDULE_ON", False)
    assert schedule(synth("cloud")[ALWAYS_FREE_STACK])["State"] == "DISABLED"


@pytest.mark.parametrize("cutover", [False, True])
def test_exactly_one_reader_takes_the_queue(monkeypatch, cutover):
    # A live poll marks emails processed; with no reader they would sit
    # unread, and two readers would race (OQ-41 e, OQ-42 a).
    from infra import agentcore_stack, always_free_stack, config

    monkeypatch.setattr(config, "AGENT_CUTOVER", cutover)
    readers = [always_free_stack.queue_reader_enabled("cloud"), agentcore_stack.consumer_enabled("cloud")]
    assert readers == [not cutover, cutover]
    assert not always_free_stack.POLL_SCHEDULE_ON or any(readers)


def test_schedule_sits_in_the_tagged_group(free):
    [group] = resources(free, "AWS::Scheduler::ScheduleGroup").values()
    assert schedule(free)["GroupName"] == group["Properties"]["Name"] == "inbox-agent"


def test_scheduler_role_may_only_invoke_the_poll_function(free):
    poll_id, _ = poll_function(free)
    role_id = schedule(free)["Target"]["RoleArn"]["Fn::GetAtt"][0]
    role = resources(free, "AWS::IAM::Role")[role_id]
    [trust] = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Principal"] == {"Service": "scheduler.amazonaws.com"}
    assert trust["Condition"] == {"StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}}}
    [statement] = policy_statements(free, role_id)
    assert statement["Action"] == "lambda:InvokeFunction"
    assert statement["Resource"] == {"Fn::GetAtt": [poll_id, "Arn"]}


def test_schedules_survive_a_mode_switch_and_only_the_state_changes(templates):
    # MODE-2: switching modes keeps the schedules; only the poll's state follows the mode.
    def without_state(template):
        return {lid: {**r["Properties"], "State": None} for lid, r in resources(template, "AWS::Scheduler::Schedule").items()}

    local = synth("local")[ALWAYS_FREE_STACK]
    assert without_state(local) == without_state(templates[ALWAYS_FREE_STACK])


# Temporary queue reader (OQ-41 e, ING-2, CLS-1, TOOL-3)


def reader_function(template):
    [(logical_id, fn)] = [
        (lid, r)
        for lid, r in resources(template, "AWS::Lambda::Function").items()
        if r["Properties"]["Handler"] == "inbox_agent.reader.handler"
    ]
    return logical_id, fn


def reader_role_id(template):
    return reader_function(template)[1]["Properties"]["Role"]["Fn::GetAtt"][0]


def agent_queue_id(template):
    return [lid for lid, r in resources(template, "AWS::SQS::Queue").items() if "RedrivePolicy" in r["Properties"]][0]


def test_reader_gets_tables_topic_and_model_from_config(free):
    _, fn = reader_function(free)
    [topic_id] = resources(free, "AWS::SNS::Topic")
    assert fn["Properties"]["Environment"]["Variables"] == {
        "TRACKER_TABLE_NAME": {"Ref": logical_id_by_key(free, "company_key")},
        "PROCESSED_EMAILS_TABLE_NAME": {"Ref": logical_id_by_key(free, "message_id")},
        "ALERT_TOPIC_ARN": {"Ref": topic_id},
        "CLASSIFIER_MODEL_ID": "eu.amazon.nova-micro-v1:0",
    }


def test_reader_role_is_least_privilege(free):
    _, fn = reader_function(free)
    granted = {}
    conditions = {}
    for statement in policy_statements(free, reader_role_id(free)):
        assert statement["Effect"] == "Allow"
        for action in as_list(statement["Action"]):
            granted.setdefault(action, []).extend(render_arn(r) for r in as_list(statement["Resource"]))
            if "Condition" in statement:
                conditions[action] = statement["Condition"]
    granted = {action: sorted(arns) for action, arns in granted.items()}

    tracker = f"<{logical_id_by_key(free, 'company_key')}>"
    processed = f"<{logical_id_by_key(free, 'message_id')}>"
    queue = f"<{agent_queue_id(free)}>"
    [topic_id] = resources(free, "AWS::SNS::Topic")
    log_group = f"<{fn['Properties']['LoggingConfig']['LogGroup']['Ref']}>"
    agentcore = "arn:${Partition}:bedrock-agentcore:eu-north-1:${AccountId}"
    directory = f"{agentcore}:workload-identity-directory/default"
    workload = f"{directory}/workload-identity/inbox-agent-poller"
    vault = f"{agentcore}:token-vault/default"
    provider = f"{vault}/oauth2credentialprovider/inbox-agent-google"
    secret = (
        "arn:${Partition}:secretsmanager:eu-north-1:${AccountId}:secret:"
        "bedrock-agentcore-identity!default/oauth2/inbox-agent-google-1ed2e5b8-NDkbRv"
    )
    profile = "arn:${Partition}:bedrock:eu-north-1:${AccountId}:inference-profile/eu.amazon.nova-micro-v1:0"
    models = [
        f"arn:${{Partition}}:bedrock:{region}::foundation-model/amazon.nova-micro-v1:0"
        for region in ("eu-central-1", "eu-north-1", "eu-west-1", "eu-west-3")
    ]
    assert granted == {
        "dynamodb:Scan": [tracker],
        "dynamodb:GetItem": sorted([tracker, processed]),
        "dynamodb:PutItem": sorted([tracker, processed]),
        "sqs:ReceiveMessage": [queue],
        "sqs:ChangeMessageVisibility": [queue],
        "sqs:GetQueueUrl": [queue],
        "sqs:DeleteMessage": [queue],
        "sqs:GetQueueAttributes": [queue],
        "sns:Publish": [f"${{{topic_id}}}"],
        "logs:CreateLogStream": [log_group],
        "logs:PutLogEvents": [log_group],
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId": sorted([directory, workload]),
        "bedrock-agentcore:GetResourceOauth2Token": sorted([directory, workload, vault, provider]),
        "secretsmanager:GetSecretValue": [secret],
        "bedrock:InvokeModel": sorted([profile, *models]),
    }


def test_reader_reaches_the_regional_models_only_through_the_profile(free):
    statements = [
        s for s in policy_statements(free, reader_role_id(free)) if "foundation-model" in json.dumps(s["Resource"])
    ]
    [statement] = statements
    [(operator, values)] = statement["Condition"].items()
    assert operator == "StringEquals"
    assert render_arn(values["bedrock:InferenceProfileArn"]) == (
        "arn:${Partition}:bedrock:eu-north-1:${AccountId}:inference-profile/eu.amazon.nova-micro-v1:0"
    )


def test_only_the_reader_may_call_bedrock(free):
    callers = {
        role_id
        for role_id in resources(free, "AWS::IAM::Role")
        if any(a.startswith("bedrock:") for s in policy_statements(free, role_id) for a in as_list(s["Action"]))
    }
    assert callers == {reader_role_id(free)}


def test_reader_role_cannot_reach_the_drafts_identity(free):
    text = json.dumps([render_arn(r) for s in policy_statements(free, reader_role_id(free)) for r in as_list(s["Resource"])])
    assert "inbox-agent-drafts" not in text


def event_source(template):
    [mapping] = resources(template, "AWS::Lambda::EventSourceMapping").values()
    return mapping["Properties"]


def test_reader_reads_the_agent_queue_with_partial_failures(free):
    reader_id, fn = reader_function(free)
    props = event_source(free)
    assert props["FunctionName"] == {"Ref": reader_id}
    assert props["EventSourceArn"] == {"Fn::GetAtt": [agent_queue_id(free), "Arn"]}
    assert props["BatchSize"] == 5
    assert props["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert props["ScalingConfig"] == {"MaximumConcurrency": 2}


def test_queue_visibility_outlasts_the_reader(free):
    _, fn = reader_function(free)
    queue = resources(free, "AWS::SQS::Queue")[agent_queue_id(free)]
    assert queue["Properties"]["VisibilityTimeout"] >= 6 * fn["Properties"]["Timeout"]


@pytest.mark.parametrize(("mode", "enabled"), [("cloud", True), ("local", False)])
def test_the_reader_runs_only_in_cloud_mode(mode, enabled):
    assert event_source(synth(mode)[ALWAYS_FREE_STACK])["Enabled"] is enabled


def test_the_reader_is_off_while_the_owner_switch_is_off(monkeypatch):
    from infra import always_free_stack

    monkeypatch.setattr(always_free_stack, "QUEUE_READER_ON", False)
    assert event_source(synth("cloud")[ALWAYS_FREE_STACK])["Enabled"] is False


def test_model_id_lives_in_config_only():
    from inbox_agent import model

    from infra import config

    assert config.CLASSIFIER_MODEL_ID == f"eu.{config.CLASSIFIER_FOUNDATION_MODEL}"
    with pytest.raises(ValueError):
        model.classifier_model({})
