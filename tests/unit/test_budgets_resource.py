import json

import pytest

import budgets_resource

PROPS = {
    "NamePrefix": "inbox-agent",
    "Limits": ["1", "5", "10"],
    "Email": "alerts@example.com",
    "Tags": {"project": "inbox-agent"},
}
ACCOUNT = "111122223333"  # fictional


class FakeBudgets:
    class exceptions:
        class NotFoundException(Exception):
            pass

    def __init__(self, existing=(), fail_on_create=False):
        self.budgets = {name: None for name in existing}
        self.calls = []
        self.fail_on_create = fail_on_create

    def delete_budget(self, AccountId, BudgetName):
        self.calls.append(("delete", BudgetName))
        if BudgetName not in self.budgets:
            raise self.exceptions.NotFoundException(BudgetName)
        del self.budgets[BudgetName]

    def create_budget(self, AccountId, Budget, NotificationsWithSubscribers, ResourceTags):
        assert AccountId == ACCOUNT
        if self.fail_on_create:
            raise RuntimeError("AccessDeniedException")
        self.calls.append(("create", Budget["BudgetName"]))
        self.budgets[Budget["BudgetName"]] = {
            "Budget": Budget,
            "Notifications": NotificationsWithSubscribers,
            "Tags": ResourceTags,
        }


def event(request_type, props=PROPS, old=None):
    e = {
        "RequestType": request_type,
        "ResourceProperties": props,
        "ResponseURL": "https://example.com/response",
        "StackId": "stack",
        "RequestId": "req",
        "LogicalResourceId": "Budgets",
    }
    if old is not None:
        e["OldResourceProperties"] = old
    return e


NAMES = ["inbox-agent-1-usd", "inbox-agent-5-usd", "inbox-agent-10-usd"]


def test_create_makes_three_budgets_counting_usage_before_credits():
    client = FakeBudgets()
    budgets_resource.apply(event("Create"), ACCOUNT, client)

    assert sorted(client.budgets) == sorted(NAMES)
    for limit, name in zip(["1", "5", "10"], NAMES):
        created = client.budgets[name]
        budget = created["Budget"]
        assert budget["BudgetLimit"] == {"Amount": limit, "Unit": "USD"}
        assert budget["BudgetType"] == "COST"
        assert budget["TimeUnit"] == "MONTHLY"
        assert budget["CostTypes"] == {"IncludeCredit": False}
        [notification] = created["Notifications"]
        assert notification["Notification"]["Threshold"] == 100.0
        assert notification["Subscribers"] == [{"SubscriptionType": "EMAIL", "Address": "alerts@example.com"}]
        assert created["Tags"] == [{"Key": "project", "Value": "inbox-agent"}]


def test_update_replaces_budgets_so_a_new_email_takes_effect():
    client = FakeBudgets(existing=NAMES)
    new = {**PROPS, "Email": "new@example.com"}
    budgets_resource.apply(event("Update", props=new, old=PROPS), ACCOUNT, client)

    assert sorted(client.budgets) == sorted(NAMES)
    for name in NAMES:
        assert client.budgets[name]["Notifications"][0]["Subscribers"][0]["Address"] == "new@example.com"


def test_update_removes_a_dropped_limit():
    client = FakeBudgets(existing=NAMES)
    new = {**PROPS, "Limits": ["1", "5"]}
    budgets_resource.apply(event("Update", props=new, old=PROPS), ACCOUNT, client)

    assert sorted(client.budgets) == ["inbox-agent-1-usd", "inbox-agent-5-usd"]


def test_delete_removes_budgets_and_tolerates_missing_ones():
    client = FakeBudgets(existing=["inbox-agent-1-usd"])
    budgets_resource.apply(event("Delete"), ACCOUNT, client)

    assert client.budgets == {}
    assert [c for c in client.calls if c[0] == "delete"] == [("delete", n) for n in NAMES]


class Context:
    invoked_function_arn = f"arn:aws:lambda:eu-north-1:{ACCOUNT}:function:budgets"
    log_stream_name = "2026/10/17/[$LATEST]abc"


@pytest.fixture
def responses(monkeypatch):
    sent = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        sent.append({"method": request.get_method(), "url": request.full_url, "body": json.loads(request.data)})
        return FakeResponse()

    monkeypatch.setattr(budgets_resource.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_handler_reports_success_to_cloudformation(monkeypatch, responses):
    monkeypatch.setattr(budgets_resource, "_client", lambda: FakeBudgets())
    budgets_resource.handler(event("Create"), Context())

    [response] = responses
    assert response["method"] == "PUT"
    assert response["url"] == "https://example.com/response"
    assert response["body"]["Status"] == "SUCCESS"
    assert response["body"]["PhysicalResourceId"] == "inbox-agent-budgets"


def test_handler_reports_failure_without_leaking_the_email(monkeypatch, responses):
    monkeypatch.setattr(budgets_resource, "_client", lambda: FakeBudgets(fail_on_create=True))
    budgets_resource.handler(event("Create"), Context())

    [response] = responses
    assert response["body"]["Status"] == "FAILED"
    assert "AccessDeniedException" in response["body"]["Reason"]
    assert "alerts@example.com" not in response["body"]["Reason"]
