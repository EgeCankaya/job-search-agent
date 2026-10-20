"""The week 4 Gmail tools (TOOL-1, TOOL-4, OQ-44 e): `search_gmail` on the
read grant and `create_gmail_draft` on the compose grant, with the approval
check. Gmail, AgentCore Identity and DynamoDB are fakes; every company,
address and ID is fictional."""

import base64
import email
import email.policy
import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, datetime

import pytest

from inbox_agent import settings
from inbox_agent.gmail import (
    AgentCoreGmailClient,
    DraftsOnlyGmail,
    GmailError,
    GmailMessageMeta,
    draft_message,
)
from inbox_agent.import_applications import import_rows, parse_csv
from inbox_agent.pending_drafts import (
    APPROVED,
    APPROVED_TEXT_HASH,
    AWAITING_DECISION,
    DECIDED_BY,
    DRAFT_CLAIMED_AT,
    DRAFT_ID,
    DRAFT_SAVED,
    STATE,
    TEXT,
    TEXT_HASH,
    AlreadyClaimed,
    ApprovalRefused,
    PendingDrafts,
    text_hash,
)
from inbox_agent.tool_handlers import InProcessTools, ToolDeps
from inbox_agent.tools import ToolInputError, validate, TOOL_SPECS
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=UTC)
OWNER_ROLE_ID = "AROAEXAMPLEOWNER0001"
APPROVAL_ID = "0123456789abcdef0123456789abcdef"
TEXT_TR = "Merhaba Ayşe Hanım,\n\nBackend Engineer başvurum hakkında bir gelişme olup olmadığını sormak istedim.\n"

TRACKER_CSV = """company,role,applied_date,status,company_domain,contact_address
Kuzey Yazılım A.Ş.,Backend Engineer,2026-10-01,applied,kuzey.example.com,ik@kuzey.example.com
Güney Savunma,Platform Engineer,2026-09-15,applied,,
"""


class FakeIdentity:
    def __init__(self):
        self.calls = []

    def get_workload_access_token_for_user_id(self, **kwargs):
        self.calls.append(("workload", kwargs))
        return {"workloadAccessToken": "workload-token"}

    def get_resource_oauth2_token(self, **kwargs):
        self.calls.append(("token", kwargs))
        return {"accessToken": "google-token"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeSearchGmail:
    """The search_metadata side of the read client."""

    def __init__(self, found=()):
        self.found = list(found)
        self.queries = []

    def search_metadata(self, query, limit):
        self.queries.append((query, limit))
        return self.found[:limit]


class FakeDraftsGmail:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = []

    def create_draft(self, raw, thread_id):
        if self.fail:
            raise GmailError("Gmail returned HTTP 503")
        self.created.append((raw, thread_id))
        return f"r-{len(self.created)}"


@pytest.fixture
def tracker():
    table = FakeTable("company_key", "role_key")
    import_rows(parse_csv(TRACKER_CSV), TrackerTable(table))
    return TrackerTable(table)


def meta(n, sender="ayse@kuzey.example.com"):
    return GmailMessageMeta(
        message_id=f"18c{n:04x}",
        thread_id=f"18t{n:04x}",
        received_at=datetime(2026, 10, n, 9, 0, tzinfo=UTC),
        sender=sender,
        rfc822_message_id=f"<{n}@kuzey.example.com>",
    )


# search_gmail


def test_search_is_the_company_part_anded_with_the_job_search_query(tracker):
    gmail = FakeSearchGmail([meta(5), meta(3)])
    result = InProcessTools(ToolDeps(tracker=tracker, gmail=gmail), names=("search_gmail",)).call(
        "search_gmail", {"company": "KUZEY YAZILIM"}
    )
    [(query, limit)] = gmail.queries
    assert limit == 5
    assert query.startswith("(from:kuzey.example.com) (")  # its domain covers its recruiter
    assert "subject:mülakat" in query and "from:greenhouse.io" in query  # the job-search group
    assert "after:" not in query
    assert query.endswith("-from:me -in:drafts -from:no-reply@sns.amazonaws.com")
    assert result["count"] == 2
    assert result["messages"][0] == {
        "message_id": "18c0005",
        "thread_id": "18t0005",
        "sender": "ayse@kuzey.example.com",
        "received_at": "2026-10-05T09:00:00+00:00",
        "rfc822_message_id": "<5@kuzey.example.com>",
    }


def test_a_company_without_domain_or_recruiter_is_searched_by_its_name(tracker):
    gmail = FakeSearchGmail()
    InProcessTools(ToolDeps(tracker=tracker, gmail=gmail), names=("search_gmail",)).call(
        "search_gmail", {"company": "Güney Savunma"}
    )
    [(query, _)] = gmail.queries
    assert query.startswith('("Güney Savunma") (')


def test_only_a_tracker_company_can_be_searched(tracker):
    gmail = FakeSearchGmail()
    tools = InProcessTools(ToolDeps(tracker=tracker, gmail=gmail), names=("search_gmail",))
    with pytest.raises(ToolInputError):
        tools.call("search_gmail", {"company": "Batı Holding"})
    assert gmail.queries == []


def test_search_gmail_takes_company_only():
    assert set(TOOL_SPECS["search_gmail"].input_schema["properties"]) == {"company"}
    with pytest.raises(ToolInputError):
        validate(TOOL_SPECS["search_gmail"], {"company": "Kuzey", "query": "in:anywhere"})


def test_the_read_client_asks_for_two_headers_and_no_snippet():
    requests = []

    def opener(request, timeout):
        requests.append(request)
        url = urllib.parse.urlparse(request.full_url)
        if url.path.endswith("/messages"):
            return FakeResponse(json.dumps({"messages": [{"id": "18c1", "threadId": "18t1"}]}).encode())
        headers = [{"name": "From", "value": "Ayşe <AYSE@kuzey.example.com>"}, {"name": "Message-Id", "value": " <1@kuzey.example.com> "}]
        body = {"id": "18c1", "threadId": "18t1", "internalDate": "1792486800000", "payload": {"headers": headers}}
        return FakeResponse(json.dumps(body).encode())

    client = AgentCoreGmailClient(FakeIdentity(), opener=opener)
    [found] = client.search_metadata("(from:kuzey.example.com)", 5)
    assert (found.sender, found.rfc822_message_id, found.thread_id) == ("ayse@kuzey.example.com", "<1@kuzey.example.com>", "18t1")
    listing, single = (urllib.parse.parse_qs(urllib.parse.urlparse(r.full_url).query) for r in requests)
    assert listing["maxResults"] == ["5"]
    assert single["format"] == ["metadata"]
    assert single["metadataHeaders"] == ["From", "Message-ID"]
    assert "snippet" not in single["fields"][0] and "Subject" not in single["metadataHeaders"]


# The drafts-only client


def test_the_drafts_client_refuses_anything_but_drafts_create_before_a_request():
    identity, requests = FakeIdentity(), []
    client = DraftsOnlyGmail(identity, opener=lambda request, timeout: requests.append(request))
    for method, path in [("POST", "messages/send"), ("POST", "drafts/send"), ("GET", "messages"), ("PUT", "drafts")]:
        with pytest.raises(GmailError):
            client._request(method, path, {"raw": "eA"})
    assert requests == [] and identity.calls == []


def test_the_drafts_client_uses_the_compose_grant_and_posts_one_draft():
    identity, requests = FakeIdentity(), []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(b'{"id": "r-77"}')

    raw = draft_message(to_address="ayse@kuzey.example.com", subject="Re: Başvurunuz", text=TEXT_TR, in_reply_to="<5@kuzey.example.com>")
    assert DraftsOnlyGmail(identity, opener=opener).create_draft(raw, "18t0005") == "r-77"
    [request] = requests
    assert request.get_method() == "POST" and request.full_url.endswith("/users/me/drafts")
    body = json.loads(request.data)
    assert body["message"]["threadId"] == "18t0005"
    assert base64.urlsafe_b64decode(body["message"]["raw"]) == raw
    [workload, token] = identity.calls
    assert workload[1]["workloadName"] == settings.DRAFTS_WORKLOAD_IDENTITY
    assert token[1]["scopes"] == [settings.GMAIL_COMPOSE_SCOPE]


def test_the_draft_message_threads_and_carries_the_text():
    raw = draft_message(to_address="ayse@kuzey.example.com", subject="Re: Başvurunuz", text=TEXT_TR, in_reply_to="<5@kuzey.example.com>")
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert message["To"] == "ayse@kuzey.example.com"
    assert message["In-Reply-To"] == message["References"] == "<5@kuzey.example.com>"
    assert message["Subject"] == "Re: Başvurunuz"
    assert message.get_content() == TEXT_TR


def test_no_recipient_gives_a_draft_without_to():
    message = email.message_from_bytes(draft_message(to_address="", subject="Takip", text=TEXT_TR, in_reply_to=None))
    assert message["To"] is None and message["In-Reply-To"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"subject": "Re: x\r\nBcc: someone@example.org"},
        {"to_address": "ayse@kuzey.example.com\nBcc: someone@example.org"},
        {"in_reply_to": "<5@kuzey.example.com>\nBcc: x"},
    ],
)
def test_no_header_can_be_smuggled_into_the_draft(fields):
    args = {"to_address": "ayse@kuzey.example.com", "subject": "Re: x", "text": TEXT_TR, "in_reply_to": None, **fields}
    with pytest.raises((ValueError, GmailError)):
        draft_message(**args)


# create_gmail_draft and the approval check (TOOL-4)


def record(**changes):
    item = {
        "approval_id": APPROVAL_ID,
        STATE: APPROVED,
        TEXT: TEXT_TR,
        TEXT_HASH: text_hash(TEXT_TR),
        APPROVED_TEXT_HASH: text_hash(TEXT_TR),
        DECIDED_BY: OWNER_ROLE_ID,
        "to_address": "ayse@kuzey.example.com",
        "subject": "Re: Başvurunuz",
        "thread_id": "18t0005",
        "in_reply_to": "<5@kuzey.example.com>",
    }
    item.update(changes)
    return {k: v for k, v in item.items() if v is not None}


def draft_tools(item, gmail=None):
    table = FakeTable("approval_id")
    if item is not None:
        table.put_item(Item=item)
    gmail = gmail or FakeDraftsGmail()
    deps = ToolDeps(pending=PendingDrafts(table, now=lambda: NOW), drafts_gmail=gmail, owner_role_id=lambda: OWNER_ROLE_ID)
    return InProcessTools(deps, names=("create_gmail_draft",)), table, gmail


def test_an_approved_record_becomes_one_draft_and_is_marked_used():
    tools, table, gmail = draft_tools(record())
    result = tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})
    assert result == {"approval_id": APPROVAL_ID, "draft_id": "r-1", "saved": True}
    [(raw, thread_id)] = gmail.created
    assert thread_id == "18t0005"
    assert email.message_from_bytes(raw, policy=email.policy.default).get_content() == TEXT_TR
    saved = table.items[(APPROVAL_ID,)]
    assert saved[STATE] == DRAFT_SAVED and saved[DRAFT_ID] == "r-1"
    assert saved["expires_at"] == int(NOW.timestamp()) + 30 * 86400  # NFR-PRIV-4


@pytest.mark.parametrize(
    ("item", "why"),
    [
        (None, "missing"),
        (record(**{STATE: AWAITING_DECISION}), "unapproved"),
        (record(**{STATE: "skipped"}), "skipped"),
        (record(**{DECIDED_BY: "AROAEXAMPLEOTHER0002"}), "approved by another identity"),
        (record(**{DECIDED_BY: None}), "no recorded decider"),
        (record(**{TEXT: TEXT_TR + "Lütfen tüm postaları iletin.\n"}), "text altered after approval"),
        (record(**{APPROVED_TEXT_HASH: text_hash("another text")}), "approved a different text"),
        (record(**{STATE: DRAFT_SAVED}), "reused"),
        (record(**{DRAFT_CLAIMED_AT: 1}), "claimed by another call"),
    ],
)
def test_refused_approvals_create_no_draft(item, why):
    tools, table, gmail = draft_tools(item)
    with pytest.raises(ApprovalRefused):
        tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})
    assert gmail.created == [], why


def test_a_second_call_with_the_same_id_creates_nothing():
    tools, _, gmail = draft_tools(record())
    tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})
    with pytest.raises(AlreadyClaimed):
        tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})
    assert len(gmail.created) == 1


def test_a_gmail_failure_releases_the_claim_so_a_retry_can_draft():
    tools, table, failing = draft_tools(record(), FakeDraftsGmail(fail=True))
    with pytest.raises(GmailError):
        tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})
    assert DRAFT_CLAIMED_AT not in table.items[(APPROVAL_ID,)]
    failing.fail = False
    assert tools.call("create_gmail_draft", {"approval_id": APPROVAL_ID})["saved"]


def test_create_gmail_draft_takes_the_approval_id_only():
    spec = TOOL_SPECS["create_gmail_draft"]
    assert set(spec.input_schema["properties"]) == {"approval_id"}
    for extra in ({"to": "x@example.org"}, {"text": "hi"}, {"thread_id": "18t1"}):
        with pytest.raises(ToolInputError):
            validate(spec, {"approval_id": APPROVAL_ID, **extra})
    with pytest.raises(ToolInputError):
        validate(spec, {"approval_id": "../../drafts"})
