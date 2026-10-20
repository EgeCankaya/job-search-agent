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


_MISSING = object()


def _condition_holds(item: dict | None, expression: str, names: dict | None, values: dict | None) -> bool:
    """Only the forms the runtime code uses: clauses joined by AND, each
    attribute_exists(path), attribute_not_exists(path) or path = :value,
    where a path is names or #placeholders joined by dots. A missing item
    has no attributes, as in DynamoDB."""
    names, values = names or {}, values or {}

    def value_at(path: str):
        node = item or {}
        for part in path.split("."):
            part = names[part] if part.startswith("#") else part
            if not isinstance(node, dict) or part not in node:
                return _MISSING
            node = node[part]
        return node

    for clause in expression.split(" AND "):
        clause = clause.strip()
        if match := re.fullmatch(r"attribute_not_exists\(([\w#.]+)\)", clause):
            holds = value_at(match.group(1)) is _MISSING
        elif match := re.fullmatch(r"attribute_exists\(([\w#.]+)\)", clause):
            holds = value_at(match.group(1)) is not _MISSING
        elif match := re.fullmatch(r"([\w#.]+) = (:\w+)", clause):
            holds = value_at(match.group(1)) == values[match.group(2)]
        else:
            raise AssertionError(f"unsupported condition {clause!r}")
        if not holds:
            return False
    return True


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

    def put_item(
        self,
        Item: dict,
        ConditionExpression: str | None = None,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
    ) -> dict:
        self._check_types(Item)
        if ConditionExpression is not None and not _condition_holds(
            self.items.get(self._key(Item)), ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues
        ):
            raise FakeConditionalCheckFailed()
        self.items[self._key(Item)] = copy.deepcopy(Item)
        self.puts += 1
        return {}

    def update_item(
        self,
        Key: dict,
        UpdateExpression: str,
        ConditionExpression: str | None = None,
        ExpressionAttributeNames: dict | None = None,
        ExpressionAttributeValues: dict | None = None,
    ) -> dict:
        """Only the forms the runtime code uses: SET with comma-separated
        assignments, each `path = :v`, `path = if_not_exists(path, :v)` or
        `path = path + :v`."""
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        self._check_types(values)
        key = self._key(Key)
        existing = self.items.get(key)
        if ConditionExpression is not None and not _condition_holds(existing, ConditionExpression, names, values):
            raise FakeConditionalCheckFailed()
        item = copy.deepcopy(existing) if existing is not None else dict(Key)
        assert UpdateExpression.startswith("SET "), UpdateExpression
        # Commas inside if_not_exists(...) don't separate assignments.
        for assignment in re.split(r",\s*(?![^()]*\))", UpdateExpression[4:]):
            target, expression = (part.strip() for part in assignment.split(" = ", 1))
            parts = [names[p] if p.startswith("#") else p for p in target.split(".")]

            def current(path):
                node = item
                for part in path.split("."):
                    part = names[part] if part.startswith("#") else part
                    if not isinstance(node, dict) or part not in node:
                        return _MISSING
                    node = node[part]
                return node

            if match := re.fullmatch(r"if_not_exists\(([\w#.]+), (:\w+)\)", expression):
                found = current(match.group(1))
                value = copy.deepcopy(values[match.group(2)]) if found is _MISSING else found
            elif match := re.fullmatch(r"([\w#.]+) \+ (:\w+)", expression):
                found = current(match.group(1))
                assert found is not _MISSING, "DynamoDB rejects + on a missing attribute"
                value = found + values[match.group(2)]
            elif re.fullmatch(r":\w+", expression):
                value = copy.deepcopy(values[expression])
            else:
                raise AssertionError(f"unsupported update {assignment!r}")
            node = item
            for part in parts[:-1]:
                assert isinstance(node.get(part), dict), "DynamoDB rejects a path through a missing map"
                node = node[part]
            node[parts[-1]] = value
        self.items[key] = item
        return {}

    def delete_item(self, Key: dict) -> dict:
        self.items.pop(self._key(Key), None)
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
