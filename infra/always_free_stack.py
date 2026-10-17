"""The always-free stack (MODE-2): it stays deployed in both runtime modes.

Week 1 contents: the three DynamoDB tables, the one SQS queue with its
dead-letter queue, the SNS alert topic, and the budget alerts. There is no
inbound HTTP endpoint (CHT-2). EventBridge Scheduler arrives in week 2 and the
DLQ alarm in week 3.
"""

from pathlib import Path

from aws_cdk import (
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct

from infra import config

SRC = Path(__file__).resolve().parents[1] / "src"
BUDGETS_SRC = SRC / "budgets"

TTL_ATTRIBUTE = "expires_at"
QUEUE_RETENTION = Duration.days(14)  # NFR-REL-2 and ING-3
MAX_RECEIVE_COUNT = 3  # ING-3
LOG_RETENTION = logs.RetentionDays.ONE_WEEK  # NFR-PRIV-4


class AlwaysFreeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, alert_email: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._tables()
        self._queues()
        self._alerts(alert_email)
        self._budgets(alert_email)

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
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=MAX_RECEIVE_COUNT, queue=self.dead_letter_queue
            ),
        )

    def _alerts(self, owner_email: str) -> None:
        # ALR-3: alerts reach only the owner, through the topic's single email
        # subscription. `notify_owner` publishes here and takes no recipient.
        self.alert_topic = sns.Topic(self, "AlertTopic", display_name="Inbox agent")
        self.alert_topic.add_subscription(subscriptions.EmailSubscription(owner_email))

        # Until the agent's role exists (week 3), only this account's principals
        # may publish, so the owner's `aws login` session can send the week 1
        # test alert. Week 3 narrows the Allow to the agent's role.
        topic_arn = [self.alert_topic.topic_arn]
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowPublishFromThisAccount",
                actions=["sns:Publish"],
                principals=[iam.AccountRootPrincipal()],
                resources=topic_arn,
            )
        )
        # Explicit denies use Principal "*" with a condition: that is how a
        # resource policy names "everyone else". No action or resource wildcards.
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyPublishFromOtherAccounts",
                effect=iam.Effect.DENY,
                actions=["sns:Publish"],
                principals=[iam.AnyPrincipal()],
                resources=topic_arn,
                conditions={"StringNotEquals": {"aws:PrincipalAccount": self.account}},
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
