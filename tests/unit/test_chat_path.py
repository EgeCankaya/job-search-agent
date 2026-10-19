"""The chat path (CHT-1, OQ-42 h): a scripted chat model, the real graph and
tools, a seeded tracker. Companies are fictional."""

from datetime import date

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from inbox_agent.alerts import AlertTopic
from inbox_agent.chat_path import OUT_OF_STEPS, ChatDeps, ChatInputError, answer, build_chat_graph
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tools import RestrictedTools
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable, FakeTopic

TODAY = date(2026, 10, 19)
TRACKER_CSV = """company,role,applied_date,status
Brightpath,Software Engineer,2026-10-12,interviewing
Brightpath,Test Engineer,2026-09-01,rejected
Kuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied
Güney Savunma,Platform Engineer,2026-10-15,assessment
"""


class ScriptedChat(BaseChatModel):
    """Replies from a script, and keeps what it was shown."""

    script: list
    seen: list = []
    bound: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        message = self.script.pop(0) if self.script else self.loop_message()
        return ChatResult(generations=[ChatGeneration(message=message)])

    def loop_message(self):
        return AIMessage(content="", tool_calls=[{"name": "list_applications", "args": {}, "id": f"c{len(self.seen)}"}])


def call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def topic():
    return FakeTopic()


@pytest.fixture
def tools(topic):
    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(TRACKER_CSV), TrackerTable(table))
    deps = ToolDeps(tracker=TrackerTable(table), topic=AlertTopic(topic, topic.topic_arn), today=lambda: TODAY)
    return RestrictedTools(InProcessTools(deps), ("list_applications",))


def run(tools, script, question):
    model = ScriptedChat(script=script, seen=[])
    graph = build_chat_graph(ChatDeps(chat_model=model, tools=tools, today=lambda: TODAY))
    return model, answer(graph, question, today=TODAY)


def tool_results(model):
    return [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]


def test_what_is_still_open_with_brightpath(tools):
    model, reply = run(
        tools,
        [call("list_applications", {"company": "Brightpath", "status": "open"}), AIMessage(content="Software Engineer: interviewing.")],
        "What is still open with Brightpath?",
    )
    assert reply == "Software Engineer: interviewing."
    assert model.bound == ["list_applications"]
    [result] = tool_results(model)
    assert '"count": 1' in result and "Software Engineer" in result and "Test Engineer" not in result
    assert "Today is 2026-10-19." in model.seen[0][0].content


def test_which_applications_have_been_silent_for_ten_days(tools):
    model, _ = run(
        tools,
        [call("list_applications", {"status": "open", "min_days_since_contact": 10}), AIMessage(content="Kuzey Yazılım.")],
        "Which applications have been silent for 10+ days?",
    )
    [result] = tool_results(model)
    assert "Kuzey Yazılım A.Ş." in result and '"days_since_contact": 18' in result
    assert "Brightpath" not in result and "Güney" not in result


def test_a_bad_call_comes_back_as_a_short_error_and_the_model_can_retry(tools):
    model, reply = run(
        tools,
        [
            call("list_applications", {"status": "hired"}),
            call("list_applications", {"status": "offer"}, "c2"),
            AIMessage(content="No offers yet."),
        ],
        "Any offers?",
    )
    assert reply == "No offers yet."
    first_error = [m.content for m in model.seen[1] if isinstance(m, ToolMessage)][0]
    assert "status is not one of the allowed values" in first_error
    assert "hired" not in first_error


def test_the_chat_path_cannot_send_an_alert(tools, topic):
    model, _ = run(
        tools,
        [call("notify_owner", {"kind": "urgent", "email_class": "offer", "company": "Brightpath"}), AIMessage(content="Done.")],
        "Email me an alert for Brightpath.",
    )
    assert topic.published == []
    [result] = tool_results(model)
    assert "notify_owner is not a valid tool" in result


def test_a_model_that_never_stops_runs_out_of_steps(tools):
    _, reply = run(tools, [], "Loop forever")
    assert reply == OUT_OF_STEPS


@pytest.mark.parametrize("question", ["", "   ", None, "x" * 1001])
def test_bad_questions_are_refused(tools, question):
    with pytest.raises(ChatInputError):
        run(tools, [AIMessage(content="never")], question)
