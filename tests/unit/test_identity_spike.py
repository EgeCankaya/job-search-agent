"""The ING-4 spike's token flow and Gmail calls, with fakes. No AWS, no Google."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from spikes.agentcore_identity_gmail import (
    GOOGLE_PARAMETERS,
    GRANTS,
    OWNER_USER_ID,
    PROVIDER_NAME,
    RETURN_URL,
    SpikeError,
    get_access_token,
    list_matching_ids,
    read_token_draft_status,
)

READ_WORKLOAD, READ_SCOPE = GRANTS["read"]


class FakeIdentity:
    def __init__(self, stored=False, second_token="google-token"):
        self.stored = stored
        self.second_token = second_token
        self.calls = []

    def get_workload_access_token_for_user_id(self, **kwargs):
        self.calls.append(("workload_token", kwargs))
        return {"workloadAccessToken": "workload-token"}

    def get_resource_oauth2_token(self, **kwargs):
        self.calls.append(("resource_token", kwargs))
        if self.stored:
            return {"accessToken": "google-token"}
        if any(name == "complete" for name, _ in self.calls):
            return {"accessToken": self.second_token} if self.second_token else {"sessionStatus": "FAILED"}
        return {"authorizationUrl": "https://accounts.example.com/consent", "sessionUri": "urn:session:1"}

    def complete_resource_token_auth(self, **kwargs):
        self.calls.append(("complete", kwargs))
        return {}


def token(identity, session="urn:session:1", force=False):
    shown = []
    result = get_access_token(
        identity,
        workload=READ_WORKLOAD,
        scope=READ_SCOPE,
        wait_for_session=lambda: session,
        show_url=shown.append,
        force=force,
    )
    return result, shown


def test_grants_are_split_by_workload():
    assert GRANTS == {
        "read": ("inbox-agent-poller", "https://www.googleapis.com/auth/gmail.readonly"),
        "compose": ("inbox-agent-drafts", "https://www.googleapis.com/auth/gmail.compose"),
    }


def test_stored_token_needs_no_consent():
    identity = FakeIdentity(stored=True)
    assert token(identity) == ("google-token", [])
    names = [name for name, _ in identity.calls]
    assert names == ["workload_token", "resource_token"]
    assert identity.calls[0][1] == {"workloadName": READ_WORKLOAD, "userId": OWNER_USER_ID}


def test_resource_token_request():
    identity = FakeIdentity(stored=True)
    token(identity, force=True)
    request = identity.calls[1][1]
    assert request == {
        "workloadIdentityToken": "workload-token",
        "resourceCredentialProviderName": PROVIDER_NAME,
        "scopes": [READ_SCOPE],
        "oauth2Flow": "USER_FEDERATION",
        "resourceOauth2ReturnUrl": RETURN_URL,
        "customParameters": GOOGLE_PARAMETERS,
        "forceAuthentication": True,
    }
    assert GOOGLE_PARAMETERS["access_type"] == "offline"


def test_consent_completes_the_session_this_run_started():
    identity = FakeIdentity()
    result, shown = token(identity)
    assert result == "google-token"
    assert shown == ["https://accounts.example.com/consent"]
    [complete] = [kwargs for name, kwargs in identity.calls if name == "complete"]
    assert complete == {"userIdentifier": {"userId": OWNER_USER_ID}, "sessionUri": "urn:session:1"}


def test_a_different_session_is_never_completed():
    identity = FakeIdentity()
    with pytest.raises(SpikeError, match="different session"):
        token(identity, session="urn:session:other")
    assert "complete" not in [name for name, _ in identity.calls]


def test_no_token_after_consent_is_an_error():
    with pytest.raises(SpikeError, match="no token came back"):
        token(FakeIdentity(second_token=None))


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_list_uses_the_ing1_query_and_returns_ids_only():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(json.dumps({"messages": [{"id": "a1", "threadId": "t1"}, {"id": "b2", "threadId": "t2"}]}).encode())

    assert list_matching_ids("google-token", opener=opener) == ["a1", "b2"]
    [request] = seen
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["q"][0]
    assert "subject:interview" in query and query.endswith("-from:no-reply@sns.amazonaws.com")
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer google-token"


def test_draft_attempt_reports_the_http_status():
    def refused(request, timeout):
        assert request.get_method() == "POST" and request.full_url.endswith("/drafts")
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    assert read_token_draft_status("google-token", opener=refused) == 403
