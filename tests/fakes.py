"""In-memory stand-ins for the AWS clients the runtime code uses.

FakeTable follows the boto3 DynamoDB Table resource closely enough to catch
the mistakes DynamoDB itself would reject: missing or empty key attributes
and floats. Scans come back in small pages, so pagination is exercised.
"""

import copy
import json
import re


class FakeConditionalCheckFailed(Exception):
    """Shaped like botocore's ClientError for a failed condition."""

    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    PAGE_SIZE = 2

    def __init__(self, *key_names: str) -> None:
        self.key_names = key_names
        self.items: dict[tuple, dict] = {}
        self.puts = 0

    def _key(self, values: dict) -> tuple:
        key = []
        for name in self.key_names:
            value = values.get(name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"key attribute {name} must be a non-empty string")
            key.append(value)
        return tuple(key)

    @staticmethod
    def _check_types(value) -> None:
        if isinstance(value, float):
            raise TypeError("Float types are not supported. Use Decimal types instead.")
        if isinstance(value, dict):
            for v in value.values():
                FakeTable._check_types(v)
        if isinstance(value, (list, set, tuple)):
            for v in value:
                FakeTable._check_types(v)

    def get_item(self, Key: dict, ConsistentRead: bool = False) -> dict:
        item = self.items.get(self._key(Key))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, Item: dict, ConditionExpression: str | None = None) -> dict:
        self._check_types(Item)
        if ConditionExpression is not None:
            # Only the form the runtime code uses.
            match = re.fullmatch(r"attribute_not_exists\((\w+)\)", ConditionExpression)
            assert match, f"unsupported condition {ConditionExpression!r}"
            existing = self.items.get(self._key(Item))
            if existing is not None and match.group(1) in existing:
                raise FakeConditionalCheckFailed()
        self.items[self._key(Item)] = copy.deepcopy(Item)
        self.puts += 1
        return {}

    def scan(self, ExclusiveStartKey: dict | None = None) -> dict:
        keys = sorted(self.items)
        start = keys.index(self._key(ExclusiveStartKey)) + 1 if ExclusiveStartKey else 0
        page = keys[start : start + self.PAGE_SIZE]
        response = {"Items": [copy.deepcopy(self.items[k]) for k in page]}
        if start + self.PAGE_SIZE < len(keys):
            response["LastEvaluatedKey"] = dict(zip(self.key_names, page[-1]))
        return response


class FakeQueue:
    """The boto3 SQS client's send_message, for one queue."""

    def __init__(self, queue_url: str = "https://sqs.eu-north-1.amazonaws.com/111122223333/agent") -> None:
        self.queue_url = queue_url
        self.messages: list[dict] = []

    def send_message(self, QueueUrl: str, MessageBody: str) -> dict:
        assert QueueUrl == self.queue_url
        self.messages.append(json.loads(MessageBody))
        return {"MessageId": f"sqs-{len(self.messages)}"}


class FakeTopic:
    """The boto3 SNS client's publish, for one topic."""

    def __init__(self, topic_arn: str = "arn:aws:sns:eu-north-1:111122223333:alerts", failures: int = 0) -> None:
        self.topic_arn = topic_arn
        self.failures = failures
        self.published: list[dict] = []

    def publish(self, TopicArn: str, Subject: str, Message: str) -> dict:
        assert TopicArn == self.topic_arn
        if self.failures:
            self.failures -= 1
            raise ConnectionError("SNS unavailable")
        self.published.append({"Subject": Subject, "Message": Message})
        return {"MessageId": f"sns-{len(self.published)}"}
