"""The applications tracker table (TRK-1).

Rows are keyed on `company_key` + `role_key` (see matching.py). The table
object is a boto3 DynamoDB Table resource, or a fake with the same methods.
"""

STATUSES = (
    "applied",
    "recruiter_contact",
    "assessment",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)
# Open means not rejected or withdrawn (TRK-1).
CLOSED_STATUSES = frozenset({"rejected", "withdrawn"})


class TrackerTable:
    def __init__(self, table) -> None:
        self._table = table

    def get(self, company_key: str, role_key: str) -> dict | None:
        response = self._table.get_item(
            Key={"company_key": company_key, "role_key": role_key}, ConsistentRead=True
        )
        return response.get("Item")

    def put(self, item: dict) -> None:
        self._table.put_item(Item=item)

    def rows(self) -> list[dict]:
        """Every row. The tracker holds tens of rows, so a scan is fine."""
        rows: list[dict] = []
        kwargs: dict = {}
        while True:
            page = self._table.scan(**kwargs)
            rows.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return rows
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
