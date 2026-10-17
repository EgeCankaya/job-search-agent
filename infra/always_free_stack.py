"""The always-free stack (MODE-2): it stays deployed in both runtime modes.

Week 1 contents: the three DynamoDB tables, the one SQS queue with its
dead-letter queue, the WhatsApp webhook behind API Gateway, and the budget
alerts. EventBridge Scheduler arrives in week 2 and the DLQ alarm in week 3.
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_budgets as budgets,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_sqs as sqs,
)
from constructs import Construct

from infra import config

WEBHOOK_SRC = Path(__file__).resolve().parents[1] / "src" / "webhook"

TTL_ATTRIBUTE = "expires_at"
QUEUE_RETENTION = Duration.days(14)  # NFR-REL-2 and ING-3
MAX_RECEIVE_COUNT = 3  # ING-3
LOG_RETENTION = logs.RetentionDays.ONE_WEEK  # NFR-PRIV-4


class AlwaysFreeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, alert_email: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._tables()
        self._queues()
        self._webhook()
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

    def _webhook(self) -> None:
        log_group = logs.LogGroup(
            self,
            "WebhookLogGroup",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # A hand-built role instead of AWSLambdaBasicExecutionRole: write to its
        # own log group and read its own SSM parameters, nothing else.
        role = iam.Role(
            self,
            "WebhookRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="WhatsApp webhook: own log group and own SSM parameters only",
        )
        log_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameters"],
                resources=[
                    self.format_arn(service="ssm", resource="parameter", resource_name=name.lstrip("/"))
                    for name in config.WEBHOOK_PARAMETERS
                ],
            )
        )

        self.webhook_function = lambda_.Function(
            self,
            "WebhookFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="whatsapp_webhook.handler",
            code=lambda_.Code.from_asset(str(WEBHOOK_SRC)),
            role=role,
            log_group=log_group,
            memory_size=128,
            timeout=Duration.seconds(10),
            environment={
                "SSM_META_VERIFY_TOKEN": config.SSM_META_VERIFY_TOKEN,
                "SSM_META_ACCESS_TOKEN": config.SSM_META_ACCESS_TOKEN,
                "SSM_META_APP_SECRET": config.SSM_META_APP_SECRET,
                "SSM_META_PHONE_NUMBER_ID": config.SSM_META_PHONE_NUMBER_ID,
                "SSM_OWNER_WHATSAPP_NUMBER": config.SSM_OWNER_WHATSAPP_NUMBER,
            },
        )

        # Stage throttling caps the cost if the public URL is flooded (§17).
        api = apigwv2.HttpApi(self, "WebhookApi", create_default_stage=False)
        apigwv2.HttpStage(
            self,
            "WebhookApiStage",
            http_api=api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigwv2.ThrottleSettings(rate_limit=10, burst_limit=20),
        )
        api.add_routes(
            path="/whatsapp",
            methods=[apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("WebhookIntegration", self.webhook_function),
        )

        CfnOutput(self, "WebhookUrl", value=f"{api.api_endpoint}/whatsapp")

    def _budgets(self, alert_email: str) -> None:
        # NFR-COST-1. Defaults count credits and taxes, so these fire on real
        # charges to the card, including KDV (NFR-COST-6, PRD §15).
        for limit in config.BUDGET_LIMITS_USD:
            budgets.CfnBudget(
                self,
                f"Budget{limit}Usd",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"inbox-agent-{limit}-usd",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=limit, unit="USD"),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="ACTUAL",
                            comparison_operator="GREATER_THAN",
                            threshold=100,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                subscription_type="EMAIL", address=alert_email
                            )
                        ],
                    )
                ],
                # Tags.of() does not reach CfnBudget, so tag it here (NFR-COST-3).
                resource_tags=[budgets.CfnBudget.ResourceTagProperty(key=config.PROJECT_TAG[0], value=config.PROJECT_TAG[1])],
            )
