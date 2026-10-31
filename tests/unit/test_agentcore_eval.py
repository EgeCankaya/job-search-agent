"""AgentCore Evaluations beside M3 (OQ-47 (g)): what is sent, and that a
failed result is counted. The Evaluate client and the spans are fakes."""

import contextlib
from types import SimpleNamespace

from eval import agentcore_eval, run_chat
from eval.chat_suite import load_questions

QUESTIONS = load_questions()


def fake_span(span_id=2, parent=1, name="list_applications", attributes=None):
    return SimpleNamespace(
        context=SimpleNamespace(trace_id=0xABC, span_id=span_id),
        parent=SimpleNamespace(span_id=parent) if parent else None,
        name=name,
        kind=SimpleNamespace(name="INTERNAL"),
        instrumentation_scope=SimpleNamespace(name="openinference.instrumentation.langchain", version="0.1.79"),
        start_time=100,
        end_time=250,
        attributes=attributes or {"openinference.span.kind": "TOOL", "tool.name": name, "session.id": "s"},
        status=SimpleNamespace(status_code=SimpleNamespace(name="OK")),
    )


def test_a_span_becomes_the_document_evaluate_reads():
    document = agentcore_eval.span_document(fake_span())
    assert document["traceId"] == f"{0xABC:032x}" and document["spanId"] == f"{2:016x}" and document["parentSpanId"] == f"{1:016x}"
    assert document["scope"]["name"] == "openinference.instrumentation.langchain"
    assert document["durationNano"] == 150 and document["status"] == {"code": "OK"}
    assert "parentSpanId" not in agentcore_eval.span_document(fake_span(parent=None))


class FakeClient:
    def __init__(self, fail=()):
        self.requests, self.fail = [], set(fail)

    def evaluate(self, **request):
        self.requests.append(request)
        if request["evaluatorId"] in self.fail:
            return {"evaluationResults": [{"evaluatorId": request["evaluatorId"], "errorCode": "ValidationException", "tokenUsage": {"inputTokens": 5, "outputTokens": 0}}]}
        return {"evaluationResults": [{"evaluatorId": request["evaluatorId"], "value": 1.0, "label": "Yes", "tokenUsage": {"inputTokens": 900, "outputTokens": 150}}]}


class FakeCapture:
    def __init__(self):
        self.sessions = []

    def session(self, session_id):
        self.sessions.append(session_id)
        return contextlib.nullcontext()

    def take(self):
        return [agentcore_eval.span_document(fake_span(span_id=1, parent=None, name="LangGraph")), agentcore_eval.span_document(fake_span())]

    def stop(self):
        pass


def test_each_question_sends_its_own_spans_and_references():
    client = FakeClient()
    evaluation = agentcore_eval.Evaluation(client, FakeCapture())
    question = next(q for q in QUESTIONS if q["id"] == "c02")
    assert evaluation.ask(question, lambda: "done") == "done"
    tool, correct = client.requests
    assert tool["evaluatorId"] == "Builtin.ToolSelectionAccuracy" and correct["evaluatorId"] == "Builtin.Correctness"
    [reference] = tool["evaluationReferenceInputs"]
    assert reference["expectedTrajectory"] == {"toolNames": ["list_applications"]}
    assert reference["context"]["spanContext"]["sessionId"].startswith("inbox-agent-ev3-c02-")
    assert "Helix Harbor" in correct["evaluationReferenceInputs"][0]["expectedResponse"]["text"]
    # The expected response is trace-level: the root span's trace.
    assert correct["evaluationReferenceInputs"][0]["context"]["spanContext"]["traceId"] == f"{0xABC:032x}"
    assert "traceId" not in reference["context"]["spanContext"]
    assert len(tool["evaluationInput"]["sessionSpans"]) == 2


def test_a_question_with_no_call_expects_no_tools():
    question = next(q for q in QUESTIONS if q["expected_calls"] == [[]])
    assert agentcore_eval.expected_tools(question) == []


def test_failed_results_are_counted_not_hidden():
    evaluation = agentcore_eval.Evaluation(FakeClient(fail={"Builtin.Correctness"}), FakeCapture())
    for question in QUESTIONS[:3]:
        evaluation.ask(question, lambda: None)
    out = evaluation.summary()
    assert out["evaluators"]["Builtin.ToolSelectionAccuracy"] == {"scored": 3, "mean": 1.0, "labels": {"Yes": 3}, "failed": 0, "failure_codes": []}
    assert out["evaluators"]["Builtin.Correctness"]["failed"] == 3
    assert out["evaluators"]["Builtin.Correctness"]["failure_codes"] == ["ValidationException"]
    assert out["judge_tokens"] == {"input": 3 * 900 + 3 * 5, "output": 3 * 150}


def test_run_chat_passes_each_question_through_the_evaluation():
    from tests.unit.test_replay import ScriptedChat

    seen = []

    class Recording:
        def ask(self, question, run):
            seen.append(question["id"])
            return run()

    results = run_chat.run(QUESTIONS[:2], {"chat": run_chat.MeteredChat(ScriptedChat(), "m")}, Recording())
    assert seen == ["c01", "c02"] and len(results) == 2
