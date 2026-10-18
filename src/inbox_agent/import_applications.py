"""TRK-3: one-off import of the owner's current applications from a CSV file.

Columns: company, role, applied date, status, and optionally company domain
and contact address. Comma or semicolon separated (Excel in a Turkish locale
writes semicolons); dates as YYYY-MM-DD or DD.MM.YYYY.

Rows go through the TRK-1 normalization, so importing the same file twice
leaves the same rows. The CSV stays on the Mac; `.gitignore` excludes it.
The script prints counts only, never company names.

    uv run python import_applications.py data/applications.csv --table <ApplicationsTableName>
"""

import argparse
import csv
import io
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from inbox_agent import settings
from inbox_agent.matching import company_key, role_key
from inbox_agent.query import normalize_address, normalize_domain
from inbox_agent.tracker import STATUSES, TrackerTable

REQUIRED_COLUMNS = ("company", "role", "applied_date", "status")
OPTIONAL_COLUMNS = ("company_domain", "contact_address")
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y")


class CsvError(ValueError):
    """The CSV can't be imported. Nothing has been written."""


@dataclass(frozen=True)
class ImportRow:
    company: str
    role: str
    applied_date: date
    status: str
    company_domain: str | None = None
    contact_address: str | None = None


@dataclass(frozen=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


def _column(header: str) -> str:
    return re.sub(r"[\s-]+", "_", header.strip().lower())


def _parse_date(value: str) -> date:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("expected YYYY-MM-DD or DD.MM.YYYY")


def parse_csv(text: str) -> list[ImportRow]:
    """Every row, validated. Raises CsvError listing every problem by line."""
    try:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {_column(name): name for name in reader.fieldnames or []}
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise CsvError(f"missing columns: {', '.join(missing)}")

    rows: list[ImportRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, record in enumerate(reader, start=2):
        values = {name: (record.get(columns[name]) or "").strip() for name in columns}
        if not any(values.values()):
            continue
        problems = []
        for name in REQUIRED_COLUMNS:
            if not values[name]:
                problems.append(f"{name} is empty")
        status = values["status"].lower().replace(" ", "_")
        if values["status"] and status not in STATUSES:
            problems.append(f"status must be one of {', '.join(STATUSES)}")
        applied = None
        if values["applied_date"]:
            try:
                applied = _parse_date(values["applied_date"])
            except ValueError as error:
                problems.append(f"applied_date: {error}")
        optional = {}
        for name, normalize in (("company_domain", normalize_domain), ("contact_address", normalize_address)):
            if values.get(name):
                try:
                    optional[name] = normalize(values[name])
                except ValueError as error:
                    problems.append(f"{name}: {error}")
        if values["company"] and values["role"]:
            try:
                key = (company_key(values["company"]), role_key(values["role"]))
            except ValueError as error:
                problems.append(str(error))
            else:
                if key in seen:
                    problems.append(f"same application as line {seen[key]} after normalization")
                seen.setdefault(key, line)
        if problems:
            errors.extend(f"line {line}: {problem}" for problem in problems)
            continue
        rows.append(
            ImportRow(
                company=values["company"],
                role=values["role"],
                applied_date=applied,
                status=status,
                **optional,
            )
        )
    if errors:
        raise CsvError("\n".join(errors))
    return rows


def _merged(existing: dict | None, row: ImportRow) -> dict:
    item = dict(existing or {})
    item.update(
        company_key=company_key(row.company),
        role_key=role_key(row.role),
        company=row.company,
        role=row.role,
        status=row.status,
        applied_date=row.applied_date.isoformat(),
    )
    # An imported application has had no inbound email yet; its applied date
    # stands in as last contact, so the stale check (APR-1) can see it.
    item.setdefault("last_contact", row.applied_date.isoformat())
    for field, value in (("company_domains", row.company_domain), ("recruiter_senders", row.contact_address)):
        values = set(item.get(field, [])) | ({value} if value else set())
        if values:
            item[field] = sorted(values)
    return item


def import_rows(rows: list[ImportRow], tracker: TrackerTable) -> ImportResult:
    created = updated = unchanged = 0
    for row in rows:
        existing = tracker.get(company_key(row.company), role_key(row.role))
        item = _merged(existing, row)
        if item == existing:
            unchanged += 1
            continue
        tracker.put(item)
        if existing is None:
            created += 1
        else:
            updated += 1
    return ImportResult(created=created, updated=updated, unchanged=unchanged)


def _dynamodb_table(name: str):
    import boto3

    return boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_applications", description=__doc__.split("\n\n")[0])
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--table", required=True, help="the ApplicationsTableName stack output")
    args = parser.parse_args(argv)

    try:
        rows = parse_csv(args.csv_path.read_text(encoding="utf-8-sig"))
    except CsvError as error:
        print(f"Nothing imported:\n{error}", file=sys.stderr)
        return 1
    result = import_rows(rows, TrackerTable(_dynamodb_table(args.table)))
    print(f"created={result.created} updated={result.updated} unchanged={result.unchanged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
