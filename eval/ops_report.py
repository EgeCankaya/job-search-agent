"""M5 and M6 (OQ-47 (h), (i)): latency and cost from the deployed system's
own records, read-only.

    uv run python eval/ops_report.py --days 7            # M5, tokens, emails processed
    uv run python eval/ops_report.py --days 7 --month    # plus Cost Explorer, month to date (one $0.01 request)

- **M5** from CloudWatch Logs Insights over the stacks' log groups: each
  Runtime `batch_done` line carries the Gmail receipt time, the poll's
  detection time and, for an alert, the publish time (NFR-OBS-1). End-to-end
  is receipt → publish, the pipeline detection → publish, both over alerts
  only; "handled" is detection → the line's own time, over every processed
  email. n, p50, p95 and max; with n < 20 the p95 is the maximum, and says
  so.
- **Tokens** per model from Bedrock's CloudWatch metrics (the live path logs
  no token counts), and emails processed from the same `batch_done` lines.
  The metrics count every call in the account, so evaluation runs from the
  Mac in the window inflate them: for M6, subtract the eval runs' own token
  counts (their scores files), or pick a window without any.
- **M6** with `--month`: Cost Explorer by service, credits and refunds not
  netted, month to date; model cost per email from the tokens at the
  estimated prices, all-in usage per email from Cost Explorer.

Logs keep 7 days, so it is run weekly and each week's JSON kept, on the Mac
(`EVAL_DATA_DIR/ops/`, never in the repo). It prints and writes counts,
times and dollars only: never a subject, a sender, a company or a message ID.
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

STACKS = ("InboxAgentAlwaysFree", "InboxAgentAgentCore")
RUNTIME_LOG_PREFIX = "/aws/bedrock-agentcore/runtimes/"
SMALL_N = 20
QUERY = 'fields @timestamp, @message | filter @message like /"event": "batch_done"/ | limit 10000'
TOKEN_METRICS = ("InputTokenCount", "OutputTokenCount", "Invocations")


# Log groups and Logs Insights


def log_groups(cloudformation, logs) -> list[str]:
    groups = []
    for stack in STACKS:
        for page in cloudformation.get_paginator("list_stack_resources").paginate(StackName=stack):
            groups += [r["PhysicalResourceId"] for r in page["StackResourceSummaries"] if r["ResourceType"] == "AWS::Logs::LogGroup"]
    for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=RUNTIME_LOG_PREFIX):
        groups += [g["logGroupName"] for g in page["logGroups"]]
    return sorted(set(groups))


def insights(logs, groups: list[str], start: datetime, end: datetime, *, sleep=time.sleep, timeout: float = 120) -> list[dict]:
    rows = []
    # A query takes at most 50 log groups.
    for i in range(0, len(groups), 50):
        query = logs.start_query(
            logGroupNames=groups[i : i + 50], startTime=int(start.timestamp()), endTime=int(end.timestamp()), queryString=QUERY
        )
        waited = 0.0
        while True:
            result = logs.get_query_results(queryId=query["queryId"])
            if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
                break
            if waited >= timeout:
                raise TimeoutError("Logs Insights query didn't finish")
            sleep(1)
            waited += 1
        if result["status"] != "Complete":
            raise RuntimeError(f"Logs Insights query {result['status']}")
        rows += [{field["field"]: field["value"] for field in row} for row in result["results"]]
    return rows


def _event(message: str) -> dict | None:
    start = message.find('{"event"')
    if start < 0:
        return None
    try:
        event, _ = json.JSONDecoder().raw_decode(message[start:])
    except json.JSONDecodeError:
        return None
    return event if event.get("event") == "batch_done" else None


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# Statistics


def percentile(values: list[float], q: float) -> float:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def stats(minutes: list[float]) -> dict:
    n = len(minutes)
    if n == 0:
        return {"n": 0}
    small = n < SMALL_N
    return {
        "n": n,
        "p50_min": round(percentile(minutes, 50), 2),
        "p95_min": round(max(minutes) if small else percentile(minutes, 95), 2),
        "p95_is_max": small,
        "max_min": round(max(minutes), 2),
    }


def latency(rows: list[dict]) -> dict:
    end_to_end, pipeline, handled, processed = [], [], [], set()
    for row in rows:
        event = _event(row.get("@message", ""))
        if event is None or event.get("duplicate"):
            continue
        processed.add(event.get("message_id"))
        received, detected, published = (_time(event.get(k)) for k in ("received_at", "detected_at", "published_at"))
        logged = _time(row.get("@timestamp"))
        if published and received:
            end_to_end.append((published - received).total_seconds() / 60)
        if published and detected:
            pipeline.append((published - detected).total_seconds() / 60)
        if logged and detected:
            handled.append((logged - detected).total_seconds() / 60)
    return {
        "emails_processed": len(processed),
        "alerts": len(end_to_end),
        "m5_end_to_end": stats(end_to_end),
        "m5_pipeline": stats(pipeline),
        "detection_to_handled": stats(handled),
    }


# Tokens and cost


def model_ids() -> list[str]:
    os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")
    from infra import config

    return sorted({config.CLASSIFIER_MODEL_ID, config.CHAT_MODEL_ID})


def tokens(cloudwatch, ids: list[str], start: datetime, end: datetime) -> dict:
    queries = [
        {
            "Id": f"m{i}_{j}",
            "MetricStat": {
                "Metric": {"Namespace": "AWS/Bedrock", "MetricName": metric, "Dimensions": [{"Name": "ModelId", "Value": model}]},
                "Period": 86400,
                "Stat": "Sum",
            },
        }
        for i, model in enumerate(ids)
        for j, metric in enumerate(TOKEN_METRICS)
    ]
    totals = {model: dict.fromkeys(TOKEN_METRICS, 0) for model in ids}
    token = None
    while True:
        kwargs = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end}
        if token:
            kwargs["NextToken"] = token
        page = cloudwatch.get_metric_data(**kwargs)
        for result in page["MetricDataResults"]:
            i, j = (int(x) for x in result["Id"][1:].split("_"))
            totals[ids[i]][TOKEN_METRICS[j]] += int(sum(result.get("Values", [])))
        token = page.get("NextToken")
        if not token:
            return totals


def model_cost(totals: dict) -> float | None:
    from eval.run_injection import PRICES

    cost = 0.0
    for model, counts in totals.items():
        if model not in PRICES:
            return None
        price_in, price_out = PRICES[model]
        cost += (counts["InputTokenCount"] * price_in + counts["OutputTokenCount"] * price_out) / 1_000_000
    return round(cost, 4)


def month_usage(ce, today: date) -> dict:
    """Month to date by service, before credits and refunds."""
    start = today.replace(day=1)
    result = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": (today + timedelta(days=1)).isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        Filter={"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}},
    )
    by_service = {}
    for period in result["ResultsByTime"]:
        for group in period["Groups"]:
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amount:
                by_service[group["Keys"][0]] = round(by_service.get(group["Keys"][0], 0) + amount, 4)
    return {"from": start.isoformat(), "to": today.isoformat(), "by_service_usd": by_service, "total_usd": round(sum(by_service.values()), 4)}


def report(*, cloudformation, logs, cloudwatch, ce=None, now: datetime, days: int, sleep=time.sleep) -> dict:
    start = now - timedelta(days=days)
    rows = insights(logs, log_groups(cloudformation, logs), start, now, sleep=sleep)
    out = {"window": {"from": start.isoformat(timespec="minutes"), "to": now.isoformat(timespec="minutes"), "days": days}, **latency(rows)}
    totals = tokens(cloudwatch, model_ids(), start, now)
    out["tokens"] = totals
    out["model_cost_usd"] = model_cost(totals)
    emails = out["emails_processed"]
    out["model_cost_per_email_usd"] = round(out["model_cost_usd"] / emails, 5) if emails and out["model_cost_usd"] is not None else None
    if ce is not None:
        usage = month_usage(ce, now.date())
        out["month"] = usage
    return out


def summary_lines(out: dict) -> list[str]:
    def show(name, s):
        if not s["n"]:
            return f"{name}: no data"
        p95 = f"p95 {s['p95_min']} min" + (" (n < 20: the maximum)" if s["p95_is_max"] else "")
        return f"{name}: n {s['n']}, p50 {s['p50_min']} min, {p95}, max {s['max_min']} min"

    lines = [
        f"Window: {out['window']['days']} days to {out['window']['to']}",
        f"Emails processed: {out['emails_processed']}, alerts: {out['alerts']}",
        show("M5 end-to-end (receipt → alert)", out["m5_end_to_end"]),
        show("M5 pipeline (detection → alert)", out["m5_pipeline"]),
        show("Detection → handled (all mail)", out["detection_to_handled"]),
    ]
    for model, counts in out["tokens"].items():
        lines.append(f"{model}: {counts['Invocations']} calls, {counts['InputTokenCount']} in / {counts['OutputTokenCount']} out tokens")
    lines.append(
        f"Model cost (estimated prices, evaluation runs in the window included): ${out['model_cost_usd']}, "
        f"per email ${out['model_cost_per_email_usd']}"
    )
    if "month" in out:
        lines.append(f"Month to date, before credits: ${out['month']['total_usd']} ({', '.join(f'{k} ${v}' for k, v in out['month']['by_service_usd'].items())})")
    return lines


def main(argv=None) -> int:
    import boto3

    from eval.run_injection import session_ok
    from inbox_agent import settings

    parser = argparse.ArgumentParser(description="M5 and M6 from CloudWatch and billing, read-only.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--month", action="store_true", help="add Cost Explorer, month to date ($0.01 a request)")
    parser.add_argument("--out-dir", type=Path, default=Path(os.environ.get("EVAL_DATA_DIR", Path.home() / "inbox-agent-eval")) / "ops")
    args = parser.parse_args(argv)
    if args.out_dir.resolve().is_relative_to(ROOT):
        parser.error("the report stays outside the repo")
    if not session_ok():
        print("No usable AWS session: run `aws login`, then run this again.")
        return 2
    region = settings.AWS_REGION
    out = report(
        cloudformation=boto3.client("cloudformation", region_name=region),
        logs=boto3.client("logs", region_name=region),
        cloudwatch=boto3.client("cloudwatch", region_name=region),
        ce=boto3.client("ce", region_name="us-east-1") if args.month else None,
        now=datetime.now(UTC),
        days=args.days,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"ops-{out['window']['to'][:10]}.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("\n".join(summary_lines(out)))
    print(f"Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
