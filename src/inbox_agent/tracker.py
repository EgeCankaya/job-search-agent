"""The applications tracker table (TRK-1).

Rows are keyed on `company_key` + `role_key` (see matching.py). The table
object is a boto3 DynamoDB Table resource, or a fake with the same methods.

The owner's company alias list (TRK-1) is one reserved item in the same
table, so it never lives in the public repo (OQ-41 d). Normalized company
keys hold only [0-9a-z ], so the "#" key can't collide with a row, and
`rows()` leaves the item out.
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

RESERVED_PREFIX = "#"
ALIASES_KEY = {"company_key": "#config", "role_key": "aliases"}


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
        """Every application row. The tracker holds tens of rows, so a scan is fine."""
        rows: list[dict] = []
        kwargs: dict = {}
        while True:
            page = self._table.scan(**kwargs)
            rows.extend(i for i in page.get("Items", []) if not i["company_key"].startswith(RESERVED_PREFIX))
            if "LastEvaluatedKey" not in page:
                return rows
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def aliases(self) -> dict[str, str]:
        """Variant -> canonical company name, as the owner wrote them."""
        item = self._table.get_item(Key=ALIASES_KEY, ConsistentRead=True).get("Item")
        return dict(item.get("aliases", {})) if item else {}

    def put_aliases(self, aliases: dict[str, str]) -> None:
        """Replaces the whole list."""
        self._table.put_item(Item={**ALIASES_KEY, "aliases": dict(aliases)})
