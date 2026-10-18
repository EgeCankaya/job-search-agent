"""In-memory stand-ins for the AWS clients the runtime code uses.

FakeTable follows the boto3 DynamoDB Table resource closely enough to catch
the mistakes DynamoDB itself would reject: missing or empty key attributes
and floats. Scans come back in small pages, so pagination is exercised.
"""

import copy
import json


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

    def put_item(self, Item: dict) -> dict:
        self._check_types(Item)
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
