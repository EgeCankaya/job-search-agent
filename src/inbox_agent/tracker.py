"""The applications tracker table (TRK-1).

Rows are keyed on `company_key` + `role_key` (see matching.py). The table
object is a boto3 DynamoDB Table resource, or a fake with the same methods.

The owner's company alias list (TRK-1) is one reserved item in the same
table, so it never lives in the public repo (OQ-41 d). Normalized company
keys hold only [0-9a-z ], so the "#" key can't collide with a row, and
`rows()` leaves the item out.

Each row may carry a `follow_up` map (APR-4, OQ-44 g): the approval
workflow's bookkeeping, which it updates in place. Writers of whole rows use
`put_row`, which refuses to overwrite a newer follow-up state.
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

# Follow-up state (TRK-1, OQ-20): ISO dates, the skip streak since the last
# new email, and a version the approval workflow raises on every change.
FOLLOW_UP = "follow_up"
LAST_PROPOSED = "last_proposed"
LAST_SKIPPED = "last_skipped"
LAST_EXPIRED = "last_expired"
LAST_DRAFT = "last_draft"
SKIPS_IN_A_ROW = "skips_in_a_row"
VERSION = "version"


class RowChanged(Exception):
    """The row changed between the read and the write; read it again."""


def _is_conditional_failure(error: Exception) -> bool:
    return getattr(error, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


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

    def put_row(self, item: dict, *, read: dict | None) -> None:
        """Write a whole row computed from `read`, the row as it was read
        (None for a new row). Raises RowChanged if the row was created, or
        its follow-up state changed, since: the approval workflow writes that
        state in place, and a whole-row write must not undo it."""
        if read is None:
            condition = {"ConditionExpression": "attribute_not_exists(company_key)"}
        elif FOLLOW_UP not in read:
            condition = {"ConditionExpression": f"attribute_not_exists({FOLLOW_UP})"}
        else:
            condition = {
                "ConditionExpression": f"{FOLLOW_UP}.{VERSION} = :version",
                "ExpressionAttributeValues": {":version": read[FOLLOW_UP].get(VERSION, 0)},
            }
        try:
            self._table.put_item(Item=item, **condition)
        except Exception as error:
            if _is_conditional_failure(error):
                raise RowChanged() from None
            raise

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
