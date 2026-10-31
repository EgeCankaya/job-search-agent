"""AgentCore Evaluations beside M3 (OQ-47 (g)): the EV-3 run's own spans,
captured in memory on the Mac and passed straight to `Evaluate`, with no
CloudWatch in between (route (g), if the spike shows `Evaluate` takes them).

The chat graph is instrumented with OpenInference's LangChain
instrumentation, whose spans keep their content as attributes ("unified
telemetry": `input.value`, `output.value`, `tool.name`), which is what
AgentCore reads for LangGraph. Each question runs in its own session
(`session.id`), and its spans are sent with two built-in evaluators:
- `Builtin.ToolSelectionAccuracy`, with the expected tool names, judged per
  tool call (a question that made no call gets no result);
- `Builtin.Correctness`, with a reference answer built from the question's
  facts; an expected response is trace-level, so its context names the
  question's trace as well as its session.

The spike on 2026-10-31 showed `Evaluate` takes spans captured in memory:
route (g), nothing in CloudWatch.

Their scores are reported beside M3, never gated: the judge is a model.
The spans hold only the fictional suite: the tracker in eval/chat/ and the
questions. Nothing is instrumented in the deployed Runtime (OQ-47 (h)).

Needs the `eval` dependency group (opentelemetry-sdk,
openinference-instrumentation-langchain), imported only here.
"""

from dataclasses import dataclass, field

EVALUATORS = ("Builtin.ToolSelectionAccuracy", "Builtin.Correctness")
SESSION_PREFIX = "inbox-agent-ev3-"


def span_document(span) -> dict:
    """One finished OpenTelemetry span as the JSON document `Evaluate` reads."""
    context = span.context
    document = {
        "traceId": format(context.trace_id, "032x"),
        "spanId": format(context.span_id, "016x"),
        "name": span.name,
        "kind": span.kind.name,
        "scope": {"name": span.instrumentation_scope.name, "version": span.instrumentation_scope.version or ""},
        "startTimeUnixNano": span.start_time,
        "endTimeUnixNano": span.end_time,
        "durationNano": span.end_time - span.start_time,
        "attributes": {key: (list(value) if isinstance(value, tuple) else value) for key, value in (span.attributes or {}).items()},
        "status": {"code": span.status.status_code.name},
    }
    if span.parent is not None:
        document["parentSpanId"] = format(span.parent.span_id, "016x")
    return document


def reference_answer(question: dict) -> str:
    return "The answer states: " + "; ".join(fact[0] for fact in question["facts"]) + "."


def expected_tools(question: dict) -> list[str]:
    return ["list_applications"] * len(question["expected_calls"][0])


@dataclass
class Capture:
    """The instrumentation and an in-memory exporter, set up once a run."""

    exporter: object
    provider: object
    instrumentor: object

    @classmethod
    def start(cls) -> "Capture":
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        instrumentor = LangChainInstrumentor()
        instrumentor.instrument(tracer_provider=provider)
        return cls(exporter, provider, instrumentor)

    def session(self, session_id: str):
        from openinference.instrumentation import using_session

        return using_session(session_id)

    def take(self) -> list[dict]:
        spans = [span_document(span) for span in self.exporter.get_finished_spans()]
        self.exporter.clear()
        return spans

    def stop(self) -> None:
        self.instrumentor.uninstrument()
        self.provider.shutdown()


@dataclass
class Judged:
    scores: dict = field(default_factory=dict)  # evaluator -> list of values
    labels: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)  # evaluator -> error codes
    input_tokens: int = 0
    output_tokens: int = 0


def evaluate(client, session_id: str, spans: list[dict], question: dict, judged: Judged) -> None:
    """Both evaluators on one question's session. A failed result is counted,
    never dropped."""
    session = {"spanContext": {"sessionId": session_id}}
    # An expected response is a trace-level reference: the question's own
    # trace, the one its root span (no parent) belongs to.
    roots = [span["traceId"] for span in spans if "parentSpanId" not in span]
    trace = {"spanContext": {"sessionId": session_id, "traceId": roots[-1] if roots else spans[-1]["traceId"]}} if spans else session
    references = {
        "Builtin.ToolSelectionAccuracy": {"context": session, "expectedTrajectory": {"toolNames": expected_tools(question)}},
        "Builtin.Correctness": {"context": trace, "expectedResponse": {"text": reference_answer(question)}},
    }
    for evaluator in EVALUATORS:
        try:
            response = client.evaluate(
                evaluatorId=evaluator,
                evaluationInput={"sessionSpans": spans},
                evaluationReferenceInputs=[references[evaluator]],
            )
        except Exception as error:
            judged.failed.setdefault(evaluator, []).append(type(error).__name__)
            continue
        for result in response["evaluationResults"]:
            usage = result.get("tokenUsage") or {}
            judged.input_tokens += int(usage.get("inputTokens", 0))
            judged.output_tokens += int(usage.get("outputTokens", 0))
            if result.get("errorCode") or result.get("value") is None:
                judged.failed.setdefault(evaluator, []).append(result.get("errorCode") or "no value")
                continue
            judged.scores.setdefault(evaluator, []).append(float(result["value"]))
            if result.get("label"):
                judged.labels.setdefault(evaluator, {}).setdefault(result["label"], 0)
                judged.labels[evaluator][result["label"]] += 1


def summary(judged: Judged, questions: int) -> dict:
    return {
        "evaluators": {
            name: {
                "scored": len(judged.scores.get(name, [])),
                "mean": round(sum(judged.scores[name]) / len(judged.scores[name]), 3) if judged.scores.get(name) else None,
                "labels": judged.labels.get(name, {}),
                "failed": len(judged.failed.get(name, [])),
                "failure_codes": sorted(set(judged.failed.get(name, []))),
            }
            for name in EVALUATORS
        },
        "questions": questions,
        "judge_tokens": {"input": judged.input_tokens, "output": judged.output_tokens},
    }


class Evaluation:
    """Wraps each question's run: its own session, its spans, the judges."""

    def __init__(self, client, capture) -> None:
        self.client, self.capture, self.judged = client, capture, Judged()
        self.questions = 0

    def ask(self, question: dict, run):
        import uuid

        session_id = f"{SESSION_PREFIX}{question['id']}-{uuid.uuid4().hex[:12]}"
        with self.capture.session(session_id):
            result = run()
        evaluate(self.client, session_id, self.capture.take(), question, self.judged)
        self.questions += 1
        return result

    def summary(self) -> dict:
        return summary(self.judged, self.questions)
