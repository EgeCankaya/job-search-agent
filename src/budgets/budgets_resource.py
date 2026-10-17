"""CloudFormation custom resource that owns the AWS Budgets alerts (NFR-COST-1).

AWS::Budgets::Budget does not exist in eu-north-1, and this account can't
create resources in any other Region, so the stack manages the budgets
through the Budgets API (a global service) instead. Create and Update
replace the budgets to match the properties; Delete removes them.

Logs never carry the alert email address.
"""

import json
import logging
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PHYSICAL_ID = "inbox-agent-budgets"


def budget_names(props: dict) -> list[str]:
    return [f"{props['NamePrefix']}-{limit}-usd" for limit in props["Limits"]]


def handler(event: dict, context: object) -> None:
    status, reason = "SUCCESS", ""
    try:
        account = context.invoked_function_arn.split(":")[4]
        apply(event, account, _client())
    except Exception as error:
        logger.exception("budgets_%s=failed", event.get("RequestType", "").lower())
        status = "FAILED"
        reason = f"{type(error).__name__}: {error} (log stream {context.log_stream_name})"[:500]
    _respond(event, status, reason)


def apply(event: dict, account: str, client) -> None:
    props = event["ResourceProperties"]
    request = event["RequestType"]

    if request == "Delete":
        for name in budget_names(props):
            _delete(client, account, name)
        logger.info("budgets_deleted=%d", len(props["Limits"]))
        return

    # On Update, drop budgets whose limit is no longer listed.
    old = event.get("OldResourceProperties")
    if old:
        for name in set(budget_names(old)) - set(budget_names(props)):
            _delete(client, account, name)

    for limit, name in zip(props["Limits"], budget_names(props)):
        _delete(client, account, name)
        client.create_budget(
            AccountId=account,
            Budget={
                "BudgetName": name,
                "BudgetType": "COST",
                "TimeUnit": "MONTHLY",
                "BudgetLimit": {"Amount": str(limit), "Unit": "USD"},
                # Free plan: credits cover everything, so count usage before credits.
                "CostTypes": {"IncludeCredit": False},
            },
            NotificationsWithSubscribers=[
                {
                    "Notification": {
                        "NotificationType": "ACTUAL",
                        "ComparisonOperator": "GREATER_THAN",
                        "Threshold": 100.0,
                        "ThresholdType": "PERCENTAGE",
                    },
                    "Subscribers": [{"SubscriptionType": "EMAIL", "Address": props["Email"]}],
                }
            ],
            ResourceTags=[{"Key": key, "Value": value} for key, value in props["Tags"].items()],
        )
    logger.info("budgets_created=%d", len(props["Limits"]))


def _delete(client, account: str, name: str) -> None:
    try:
        client.delete_budget(AccountId=account, BudgetName=name)
    except client.exceptions.NotFoundException:
        pass


def _client():
    import boto3

    return boto3.client("budgets")


def _respond(event: dict, status: str, reason: str) -> None:
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason,
            "PhysicalResourceId": PHYSICAL_ID,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
        }
    ).encode()
    request = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        headers={"Content-Type": "", "Content-Length": str(len(body))},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        logger.info("cfn_response_status=%s", response.status)
