"""The AgentCore-backed Gmail client (ING-4), with fakes. No AWS, no Google."""

import base64
import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, datetime

import pytest

from inbox_agent import settings
from inbox_agent.gmail import MAX_MESSAGES, AgentCoreGmailClient, GmailError, GmailMessageRef


class FakeIdentity:
    def __init__(self, token="google-token"):
        self.token = token
        self.calls = []

    def get_workload_access_token_for_user_id(self, **kwargs):
        self.calls.append(("workload_token", kwargs))
        return {"workloadAccessToken": "workload-token"}

    def get_resource_oauth2_token(self, **kwargs):
        self.calls.append(("resource_token", kwargs))
        if self.token:
            return {"accessToken": self.token}
        return {"authorizationUrl": "https://accounts.example.com/consent", "sessionUri": "urn:session:1"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeGmailApi:
    """Answers messages.list in pages and messages.get with id and internalDate."""

    def __init__(self, pages, error=None):
        self.pages = pages
        self.error = error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise urllib.error.HTTPError(request.full_url, self.error, "error", {}, io.BytesIO(b'{"error": {"status": "PERMISSION_DENIED"}}'))
        url = urllib.parse.urlparse(request.full_url)
        params = urllib.parse.parse_qs(url.query)
        if url.path.endswith("/messages"):
            page = int(params.get("pageToken", ["0"])[0])
            body = {"messages": [{"id": i, "threadId": f"t-{i}"} for i in self.pages[page]]}
            if page + 1 < len(self.pages):
                body["nextPageToken"] = str(page + 1)
            return FakeResponse(json.dumps(body).encode())
        message_id = url.path.rsplit("/", 1)[1]
        body = {"id": message_id, "internalDate": "1792314000000"}
        if params.get("format") == ["raw"]:
            body["raw"] = base64.urlsafe_b64encode(RAW_EMAIL).decode().rstrip("=")
        return FakeResponse(json.dumps(body).encode())


RAW_EMAIL = (
    "From: =?utf-8?q?=C4=B0K?= <ik@kuzey.example.com>\r\n"
    "Subject: =?utf-8?q?M=C3=BClakat_daveti?=\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
    "--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nMerhaba, mülakat için davet.\r\n"
    "--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Merhaba</p>\r\n"
    "--b\r\nContent-Type: text/calendar; charset=utf-8; method=REQUEST\r\n\r\n"
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20261103T110000Z\r\nSUMMARY:Teknik mülakat\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    "--b--\r\n"
).encode("utf-8")


def test_token_comes_from_the_pollers_workload_identity_for_the_owner():
    identity = FakeIdentity()
    AgentCoreGmailClient(identity, opener=FakeGmailApi([[]])).search("q")
    (_, workload), (_, resource) = identity.calls
    assert workload == {"workloadName": "inbox-agent-poller", "userId": "owner"}
    assert resource == {
        "workloadIdentityToken": "workload-token",
        "resourceCredentialProviderName": "inbox-agent-google",
        "scopes": [settings.GMAIL_READ_SCOPE],
        "oauth2Flow": "USER_FEDERATION",
        "resourceOauth2ReturnUrl": settings.OAUTH_RETURN_URL,
        "customParameters": settings.GOOGLE_OAUTH_PARAMETERS,
    }
    assert "forceAuthentication" not in resource


def test_read_scope_only():
    identity = FakeIdentity()
    AgentCoreGmailClient(identity, opener=FakeGmailApi([[]])).search("q")
    assert identity.calls[1][1]["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_missing_consent_fails_without_a_link():
    with pytest.raises(GmailError, match="--grant read") as error:
        AgentCoreGmailClient(FakeIdentity(token=None), opener=FakeGmailApi([[]])).search("q")
    assert "accounts.example.com" not in str(error.value)


def test_search_pages_and_returns_ids_and_receipt_times():
    api = FakeGmailApi([["a1", "b2"], ["c3"]])
    refs = AgentCoreGmailClient(FakeIdentity(), opener=api).search("subject:interview")
    received = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    assert refs == [
        GmailMessageRef("a1", "t-a1", received),
        GmailMessageRef("b2", "t-b2", received),
        GmailMessageRef("c3", "t-c3", received),
    ]
    first = urllib.parse.urlparse(api.requests[0].full_url)
    assert urllib.parse.parse_qs(first.query)["q"] == ["subject:interview"]
    assert api.requests[0].get_header("Authorization") == "Bearer google-token"


def test_message_lookups_never_ask_for_the_snippet():
    api = FakeGmailApi([["a1"]])
    AgentCoreGmailClient(FakeIdentity(), opener=api).search("q")
    get = urllib.parse.urlparse(api.requests[-1].full_url)
    params = urllib.parse.parse_qs(get.query)
    assert params == {"format": ["minimal"], "fields": ["id,internalDate"]}


def test_only_get_requests():
    api = FakeGmailApi([["a1"], ["b2"]])
    AgentCoreGmailClient(FakeIdentity(), opener=api).search("q")
    assert {request.get_method() for request in api.requests} == {"GET"}


def test_a_runaway_query_stops():
    pages = [[f"m{p}-{i}" for i in range(100)] for p in range(MAX_MESSAGES // 100 + 1)]
    with pytest.raises(GmailError, match="too wide"):
        AgentCoreGmailClient(FakeIdentity(), opener=FakeGmailApi(pages)).search("q")


def test_gmail_errors_name_the_status_only():
    with pytest.raises(GmailError, match="HTTP 403 PERMISSION_DENIED"):
        AgentCoreGmailClient(FakeIdentity(), opener=FakeGmailApi([[]], error=403)).search("q")


# fetch: one queued message, read in full (read_email)


def test_fetch_reads_one_message_with_the_read_grant():
    identity = FakeIdentity()
    api = FakeGmailApi([[]])
    fetched = AgentCoreGmailClient(identity, opener=api).fetch("18c0ffee")
    assert identity.calls[1][1]["scopes"] == [settings.GMAIL_READ_SCOPE]
    [request] = api.requests
    url = urllib.parse.urlparse(request.full_url)
    assert url.path.endswith("/messages/18c0ffee")
    assert urllib.parse.parse_qs(url.query) == {"format": ["raw"], "fields": ["id,internalDate,raw"]}
    assert request.get_method() == "GET"

    assert fetched.header.message_id == "18c0ffee"
    assert fetched.header.sender == "İK <ik@kuzey.example.com>"
    assert fetched.header.subject == "Mülakat daveti"
    assert fetched.header.received_at == datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    assert fetched.text.strip() == "Merhaba, mülakat için davet."
    assert "<p>Merhaba</p>" in fetched.html
    assert fetched.invite.start == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["../drafts", "a b", "", "x" * 65, "id?format=full"])
def test_fetch_refuses_anything_but_a_message_id(bad):
    api = FakeGmailApi([[]])
    with pytest.raises(GmailError, match="not a Gmail message ID"):
        AgentCoreGmailClient(FakeIdentity(), opener=api).fetch(bad)
    assert api.requests == []
