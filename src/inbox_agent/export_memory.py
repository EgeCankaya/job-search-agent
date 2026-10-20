"""MODE-3 and the week 4 exit check 4: copy AgentCore Memory's long-term
records into the local SQLite store on the Mac.

AgentCore has no bulk export (OQ-4), so this pages through
`ListMemoryRecords` for each namespace prefix, stores every record, and
prints both counts: the records AgentCore listed and the records the local
store holds. `--show N` prints N records in full for the spot check; nothing
else prints record text. The store lives under `data/`, which git ignores.

Run with the owner's `aws login` session, from the repo root.
"""

import argparse
from collections.abc import Iterator

from inbox_agent import settings
from inbox_agent.memory import DEFAULT_LOCAL_PATH, PREFERENCES_NAMESPACE, SUMMARIES_NAMESPACE, SqliteMemory

NAMESPACES = (PREFERENCES_NAMESPACE, SUMMARIES_NAMESPACE)
MEMORY_NAME = "inbox_agent_memory"  # infra/config.py; AgentCore adds "-<suffix>" to the ID
PAGE_SIZE = 100


def find_memory_id(control) -> str:
    found = []
    kwargs: dict = {}
    while True:
        page = control.list_memories(**kwargs)
        found += [m["id"] for m in page.get("memories", []) if m["id"].startswith(MEMORY_NAME + "-")]
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    if len(found) != 1:
        raise SystemExit(f"expected one {MEMORY_NAME} memory, found {len(found)}")
    return found[0]


def list_records(client, memory_id: str, namespace: str) -> Iterator[dict]:
    kwargs = {"memoryId": memory_id, "namespace": namespace, "maxResults": PAGE_SIZE}
    while True:
        page = client.list_memory_records(**kwargs)
        for summary in page.get("memoryRecordSummaries", []):
            created = summary["createdAt"]
            yield {
                "record_id": summary["memoryRecordId"],
                "namespace": summary["namespaces"][0],
                "text": summary["content"].get("text", ""),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            }
        if not page.get("nextToken"):
            return
        kwargs["nextToken"] = page["nextToken"]


def export(client, memory_id: str, store: SqliteMemory) -> tuple[int, int, list[dict]]:
    """Records listed, records the store holds, and the records."""
    records = {}
    for namespace in NAMESPACES:
        for record in list_records(client, memory_id, namespace):
            records[record["record_id"]] = record
    held = store.import_records(list(records.values()))
    return len(records), held, list(records.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_LOCAL_PATH), help="the local SQLite file")
    parser.add_argument("--show", type=int, default=0, help="print this many records in full")
    args = parser.parse_args(argv)

    import boto3

    session = boto3.Session(region_name=settings.AWS_REGION)
    memory_id = find_memory_id(session.client("bedrock-agentcore-control"))
    listed, held, records = export(session.client("bedrock-agentcore"), memory_id, SqliteMemory(args.out))
    print(f"listed in AgentCore Memory: {listed}")
    print(f"held in {args.out}: {held}")
    print("counts match" if listed == held else "COUNTS DIFFER")
    for record in records[: max(args.show, 0)]:
        print(f"\n[{record['namespace']}] {record['created_at']}\n{record['text']}")
    return 0 if listed == held else 1
