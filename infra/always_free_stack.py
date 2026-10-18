"""The always-free stack (MODE-2): it stays deployed in both runtime modes.

Contents: the three DynamoDB tables, the one SQS queue with its dead-letter
queue, the SNS alert topic, the budget alerts, the Gmail poll Lambda with
its 30-minute EventBridge Scheduler schedule, and the temporary week 2 queue
reader (OQ-41 e). There is no inbound HTTP endpoint (CHT-2). The DLQ alarm
arrives in week 3.
"""

from pathlib import Path

from aws_cdk import (
    ArnFormat,
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as event_sources,
    aws_logs as logs,
    aws_scheduler as scheduler,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct

from infra import config

SRC = Path(__file__).resolve().parents[1] / "src"
BUDGETS_SRC = SRC / "budgets"
AGENT_PACKAGE = "inbox_agent"

TTL_ATTRIBUTE = "expires_at"
QUEUE_RETENTION = Duration.days(14)  # NFR-REL-2 and ING-3
MAX_RECEIVE_COUNT = 3  # ING-3
LOG_RETENTION = logs.RetentionDays.ONE_WEEK  # NFR-PRIV-4
POLL_RATE = "rate(30 minutes)"  # ING-1
READER_TIMEOUT = Duration.seconds(60)
# AWS's advice for a Lambda reading SQS: visibility at least 6 x the timeout,
# so a slow attempt isn't handed to a second reader mid-run.
QUEUE_VISIBILITY = Duration.seconds(6 * 60)
READER_BATCH_SIZE = 5
READER_MAX_CONCURRENCY = 2  # the lowest SQS allows; keeps tracker writes nearly serial
# Turned on by the owner on 2026-10-18, after a deploy and a clean dry run.
QUEUE_READER_ON = True
# Turned on by the owner on 2026-10-18, with the queue reader: a live poll
# marks emails processed, so it must never run without a reader.
POLL_SCHEDULE_ON = True


def poll_schedule_state(agent_mode: str) -> str:
    """ENABLED only in cloud mode. In MacBook mode the Mac worker polls, with
    the read token in the Keychain (ING-1, MODE-2, OQ-41 c)."""
    return "ENABLED" if POLL_SCHEDULE_ON and agent_mode == "cloud" else "DISABLED"


def queue_reader_enabled(agent_mode: str) -> bool:
    """Cloud mode only: its Gmail token lives in AgentCore Identity (ING-4)."""
    return QUEUE_READER_ON and agent_mode == "cloud"


class AlwaysFreeStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, alert_email: str, agent_mode: str = "cloud", **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.agent_mode = agent_mode

        self._tables()
        self._queues()
        self.alert_topic = sns.Topic(self, "AlertTopic", display_name="Inbox agent")
        self._budgets(alert_email)
        self._poller()
        self._reader()
        self._alerts(alert_email, publisher=self.reader_function.role)

    def _tables(self) -> None:
        # Tracker rows are kept until the project ends (NFR-PRIV-4), so the
        # table survives `cdk destroy`. Keys follow TRK-1 matching.
        self.applications_table = dynamodb.Table(
            self,
            "ApplicationsTable",
            partition_key=dynamodb.Attribute(name="company_key", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="role_key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        # The TRK-3 import script takes this name as --table.
        CfnOutput(self, "ApplicationsTableName", value=self.applications_table.table_name)

        # ING-2. Entries expire 30 days after processing (NFR-PRIV-4).
        self.processed_emails_table = dynamodb.Table(
            self,
            "ProcessedEmailsTable",
            partition_key=dynamodb.Attribute(name="message_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute=TTL_ATTRIBUTE,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # APR-3. Records expire 30 days after they are resolved (NFR-PRIV-4).
        self.pending_drafts_table = dynamodb.Table(
            self,
            "PendingDraftsTable",
            partition_key=dynamodb.Attribute(name="approval_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute=TTL_ATTRIBUTE,
            removal_policy=RemovalPolicy.DESTROY,
        )

    def _queues(self) -> None:
        # ING-3: one queue for every trigger; dead letters after 3 receives.
        self.dead_letter_queue = sqs.Queue(
            self,
            "AgentDeadLetterQueue",
            retention_period=QUEUE_RETENTION,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        self.agent_queue = sqs.Queue(
            self,
            "AgentQueue",
            retention_period=QUEUE_RETENTION,
            visibility_timeout=QUEUE_VISIBILITY,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=MAX_RECEIVE_COUNT, queue=self.dead_letter_queue
            ),
        )

    def _alerts(self, owner_email: str, *, publisher: iam.IRole) -> None:
        # ALR-3: alerts reach only the owner, through the topic's single email
        # subscription. The publisher takes no recipient.
        self.alert_topic.add_subscription(subscriptions.EmailSubscription(owner_email))

        # Only the publisher's role may publish: in week 2 the temporary queue
        # reader, from week 3 the agent (OQ-41 e). The Deny also stops other
        # principals in this account, such as the owner's `aws login` session,
        # whose own policy would otherwise allow it.
        topic_arn = [self.alert_topic.topic_arn]
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowPublishFromThePublisher",
                actions=["sns:Publish"],
                principals=[iam.ArnPrincipal(publisher.role_arn)],
                resources=topic_arn,
            )
        )
        # Explicit denies use Principal "*" with a condition: that is how a
        # resource policy names "everyone else". No action or resource wildcards.
        # A service principal carries no aws:PrincipalArn, so it is denied too;
        # the week 3 DLQ alarm needs its own exception.
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyPublishFromOtherPrincipals",
                effect=iam.Effect.DENY,
                actions=["sns:Publish"],
                principals=[iam.AnyPrincipal()],
                resources=topic_arn,
                conditions={"ArnNotEquals": {"aws:PrincipalArn": publisher.role_arn}},
            )
        )
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyPublishWithoutTls",
                effect=iam.Effect.DENY,
                actions=["sns:Publish"],
                principals=[iam.AnyPrincipal()],
                resources=topic_arn,
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

    def _budgets(self, alert_email: str) -> None:
        # NFR-COST-1. AWS::Budgets::Budget doesn't exist in eu-north-1, so a
        # deploy-time Lambda creates the budgets through the global Budgets
        # API (src/budgets). They exclude credits, because on the Free plan
        # credits cover everything and a net budget would never fire (OQ-39).
        names = [f"{config.BUDGET_NAME_PREFIX}-{limit}-usd" for limit in config.BUDGET_LIMITS_USD]

        log_group = logs.LogGroup(
            self,
            "BudgetsLogGroup",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )
        role = iam.Role(
            self,
            "BudgetsRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Budgets custom resource: own log group and the project's budgets only",
        )
        log_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["budgets:ModifyBudget", "budgets:TagResource"],
                resources=[
                    self.format_arn(service="budgets", region="", resource="budget", resource_name=name)
                    for name in names
                ],
            )
        )

        function = lambda_.Function(
            self,
            "BudgetsFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="budgets_resource.handler",
            code=lambda_.Code.from_asset(str(BUDGETS_SRC)),
            role=role,
            log_group=log_group,
            memory_size=128,
            timeout=Duration.seconds(60),
        )

        CustomResource(
            self,
            "Budgets",
            service_token=function.function_arn,
            resource_type="Custom::InboxAgentBudgets",
            properties={
                "NamePrefix": config.BUDGET_NAME_PREFIX,
                "Limits": [str(limit) for limit in config.BUDGET_LIMITS_USD],
                "Email": alert_email,
                # Tags.of() can't reach budgets made through the API (NFR-COST-3).
                "Tags": {config.PROJECT_TAG[0]: config.PROJECT_TAG[1]},
            },
        )

    def _grant_gmail_read(self, role: iam.Role) -> None:
        # ING-4: the Gmail read token, from AgentCore Identity, as the poller's
        # workload identity, from the one Google provider (exact ARNs only).
        # Never the drafts identity, which holds the compose grant.
        directory = self.format_arn(
            service="bedrock-agentcore", resource="workload-identity-directory", resource_name="default"
        )
        workload = f"{directory}/workload-identity/{config.POLLER_WORKLOAD_IDENTITY}"
        vault = self.format_arn(service="bedrock-agentcore", resource="token-vault", resource_name="default")
        provider = f"{vault}/oauth2credentialprovider/{config.GOOGLE_CREDENTIAL_PROVIDER}"
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetWorkloadAccessTokenForUserId"], resources=[directory, workload]
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetResourceOauth2Token"],
                resources=[directory, workload, vault, provider],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    self.format_arn(
                        service="secretsmanager",
                        resource="secret",
                        resource_name=config.GOOGLE_CLIENT_SECRET_NAME,
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    )
                ],
            )
        )

    def _poller(self) -> None:
        # ING-1 to ING-3. The schedule lives here, not in the AgentCore stack,
        # so it survives a mode switch; only its state follows the mode (MODE-2).
        log_group = logs.LogGroup(
            self,
            "PollLogGroup",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )
        role = iam.Role(
            self,
            "PollRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Gmail poll: Gmail read token, tracker read, processed-email log, send to the agent queue",
        )
        log_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(actions=["dynamodb:Scan"], resources=[self.applications_table.table_arn])
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem"],
                resources=[self.processed_emails_table.table_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(actions=["sqs:SendMessage"], resources=[self.agent_queue.queue_arn])
        )
        self._grant_gmail_read(role)

        self.poll_function = lambda_.Function(
            self,
            "PollFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler=f"{AGENT_PACKAGE}.poll.handler",
            code=lambda_.Code.from_asset(str(SRC), exclude=["*", f"!{AGENT_PACKAGE}", f"!{AGENT_PACKAGE}/*.py"]),
            role=role,
            log_group=log_group,
            memory_size=256,
            timeout=Duration.seconds(60),
            # A failed poll isn't retried: the next one covers the same window.
            retry_attempts=0,
            environment={
                "TRACKER_TABLE_NAME": self.applications_table.table_name,
                "PROCESSED_EMAILS_TABLE_NAME": self.processed_emails_table.table_name,
                "AGENT_QUEUE_URL": self.agent_queue.queue_url,
            },
        )

        # Schedules can't carry tags; their group can (NFR-COST-3).
        group = scheduler.CfnScheduleGroup(self, "ScheduleGroup", name="inbox-agent")
        schedule_role = iam.Role(
            self,
            "PollScheduleRole",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="EventBridge Scheduler: invoke the Gmail poll function only",
        )
        schedule_role.add_to_policy(
            iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[self.poll_function.function_arn])
        )
        schedule = scheduler.CfnSchedule(
            self,
            "PollSchedule",
            description="Gmail poll every 30 minutes (ING-1)",
            group_name=group.name,
            schedule_expression=POLL_RATE,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            state=poll_schedule_state(self.agent_mode),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=self.poll_function.function_arn,
                role_arn=schedule_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_event_age_in_seconds=30 * 60, maximum_retry_attempts=2
                ),
            ),
        )
        schedule.add_resource_dependency(group)

    def _reader(self) -> None:
        # OQ-41 (e): the temporary week 2 queue reader. The week 3 agent
        # replaces it, and the topic policy moves to the agent's role.
        log_group = logs.LogGroup(
            self,
            "ReaderLogGroup",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )
        role = iam.Role(
            self,
            "ReaderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Queue reader: Gmail read token, Nova Micro, tracker, processed-email log, alert topic",
        )
        log_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem"],
                resources=[self.applications_table.table_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem"],
                resources=[self.processed_emails_table.table_arn],
            )
        )
        role.add_to_policy(iam.PolicyStatement(actions=["sns:Publish"], resources=[self.alert_topic.topic_arn]))
        self._grant_gmail_read(role)

        # TOOL-3, route 1: the `eu.` Nova Micro profile, and its Regions'
        # model only when the call comes through that profile.
        profile = self.format_arn(
            service="bedrock", resource="inference-profile", resource_name=config.CLASSIFIER_MODEL_ID
        )
        role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[profile]))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    self.format_arn(
                        service="bedrock",
                        region=region,
                        account="",
                        resource="foundation-model",
                        resource_name=config.CLASSIFIER_FOUNDATION_MODEL,
                    )
                    for region in config.CLASSIFIER_PROFILE_REGIONS
                ],
                conditions={"StringEquals": {"bedrock:InferenceProfileArn": profile}},
            )
        )

        self.reader_function = lambda_.Function(
            self,
            "ReaderFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler=f"{AGENT_PACKAGE}.reader.handler",
            code=lambda_.Code.from_asset(str(SRC), exclude=["*", f"!{AGENT_PACKAGE}", f"!{AGENT_PACKAGE}/*.py"]),
            role=role,
            log_group=log_group,
            memory_size=256,
            timeout=READER_TIMEOUT,
            environment={
                "TRACKER_TABLE_NAME": self.applications_table.table_name,
                "PROCESSED_EMAILS_TABLE_NAME": self.processed_emails_table.table_name,
                "ALERT_TOPIC_ARN": self.alert_topic.topic_arn,
                "CLASSIFIER_MODEL_ID": config.CLASSIFIER_MODEL_ID,
            },
        )
        # Failed messages go back one by one; after 3 receives, to the DLQ (ING-3).
        self.reader_function.add_event_source(
            event_sources.SqsEventSource(
                self.agent_queue,
                batch_size=READER_BATCH_SIZE,
                report_batch_item_failures=True,
                max_concurrency=READER_MAX_CONCURRENCY,
                enabled=queue_reader_enabled(self.agent_mode),
            )
        )
