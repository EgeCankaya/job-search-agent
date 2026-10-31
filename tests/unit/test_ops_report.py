"""The ops report (OQ-47 (i)): M5 with the small-n rule, tokens and cost,
counts only. AWS is faked."""

import json
from datetime import UTC, date, datetime, timedelta

from eval import ops_report

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def batch_line(minutes_ago: float, *, received_lag=5.0, detected_lag=0.5, alerted=True, duplicate=False, message_id="m1"):
    logged = NOW - timedelta(minutes=minutes_ago)
    published = logged
    event = {"event": "batch_done", "message_id": message_id, "duplicate": duplicate, "alerted": alerted, "seconds": 9.1}
    if not duplicate:
        event["received_at"] = (published - timedelta(minutes=received_lag)).isoformat()
        event["detected_at"] = (published - timedelta(minutes=detected_lag)).isoformat()
        if alerted:
            event["published_at"] = published.isoformat()
    return [{"field": "@timestamp", "value": logged.strftime("%Y-%m-%d %H:%M:%S.000")}, {"field": "@message", "value": "INFO " + json.dumps(event)}]


class FakeLogs:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def get_paginator(self, name):
        class P:
            def paginate(self, **kwargs):
                return [{"logGroups": [{"logGroupName": "/aws/bedrock-agentcore/runtimes/rt-DEFAULT"}]}]

        return P()

    def start_query(self, **kwargs):
        self.queries.append(kwargs)
        return {"queryId": "q1"}

    def get_query_results(self, queryId):
        return {"status": "Complete", "results": self.rows}


class FakeCloudFormation:
    def get_paginator(self, name):
        class P:
            def paginate(self, StackName):
                return [{"StackResourceSummaries": [{"ResourceType": "AWS::Logs::LogGroup", "PhysicalResourceId": f"/aws/lambda/{StackName}-consumer"}]}]

        return P()


class FakeCloudWatch:
    def get_metric_data(self, MetricDataQueries, StartTime, EndTime):
        values = {"InputTokenCount": [1000, 500], "OutputTokenCount": [100], "Invocations": [3]}
        metric = lambda q: q["MetricStat"]["Metric"]["MetricName"]  # noqa: E731
        return {"MetricDataResults": [{"Id": q["Id"], "Values": values[metric(q)]} for q in MetricDataQueries]}


class FakeCostExplorer:
    def __init__(self):
        self.calls = []

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ResultsByTime": [{"Groups": [{"Keys": ["Amazon Bedrock"], "Metrics": {"UnblendedCost": {"Amount": "0.042"}}}, {"Keys": ["Tax"], "Metrics": {"UnblendedCost": {"Amount": "0"}}}]}]}


def test_the_small_n_rule_reports_the_maximum_as_p95():
    assert ops_report.stats([1, 2, 3]) == {"n": 3, "p50_min": 2, "p95_min": 3, "p95_is_max": True, "max_min": 3}
    many = [float(i) for i in range(1, 41)]
    assert ops_report.stats(many)["p95_min"] == 38 and ops_report.stats(many)["p95_is_max"] is False


def test_latency_from_batch_done_lines():
    rows = [
        {f["field"]: f["value"] for f in batch_line(30, message_id="a")},
        {f["field"]: f["value"] for f in batch_line(20, alerted=False, message_id="b")},
        {f["field"]: f["value"] for f in batch_line(10, duplicate=True, message_id="a")},
    ]
    out = ops_report.latency(rows)
    assert out["emails_processed"] == 2 and out["alerts"] == 1
    assert out["m5_end_to_end"]["p50_min"] == 5.0 and out["m5_pipeline"]["max_min"] == 0.5
    assert out["detection_to_handled"]["n"] == 2


def test_the_report_holds_no_message_ids_or_text():
    logs = FakeLogs([batch_line(30, message_id="19secret1"), batch_line(20, message_id="19secret2")])
    ce = FakeCostExplorer()
    out = ops_report.report(cloudformation=FakeCloudFormation(), logs=logs, cloudwatch=FakeCloudWatch(), ce=ce, now=NOW, days=7, sleep=lambda s: None)
    text = json.dumps(out) + "\n".join(ops_report.summary_lines(out))
    assert "19secret" not in text
    assert out["emails_processed"] == 2
    model = ops_report.model_ids()[0]
    assert out["tokens"][model] == {"InputTokenCount": 1500, "OutputTokenCount": 100, "Invocations": 3}
    assert out["model_cost_per_email_usd"] is not None
    assert out["month"]["by_service_usd"] == {"Amazon Bedrock": 0.042}
    # Credits and refunds are left out, not netted; one request.
    assert ce.calls[0]["Filter"] == {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}
    assert len(ce.calls) == 1
    assert set(logs.queries[0]["logGroupNames"]) == {
        "/aws/lambda/InboxAgentAlwaysFree-consumer",
        "/aws/lambda/InboxAgentAgentCore-consumer",
        "/aws/bedrock-agentcore/runtimes/rt-DEFAULT",
    }


def test_the_month_starts_on_the_first():
    usage = ops_report.month_usage(FakeCostExplorer(), date(2026, 10, 29))
    assert usage["from"] == "2026-10-01" and usage["total_usd"] == 0.042


def test_the_report_is_refused_inside_the_repo(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        ops_report.main(["--out-dir", str(ops_report.ROOT / "eval" / "results")])
