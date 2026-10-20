"""The Gmail interface the poller, `read_email` and `search_gmail` use
(ING-1), its real client (ING-4), and the drafts-only client of
`create_gmail_draft` (TOOL-4).

The real client gets a `gmail.readonly` token from AgentCore Identity for the
owner, as the poller's workload identity, then calls the Gmail REST API with
the standard library. A search asks Gmail only for IDs and the receipt time,
never for the snippet. Only `fetch` reads email text, for one message the
poller queued; that text goes to the classifier, never into logs
(NFR-PRIV-1, NFR-PRIV-3). `search_metadata` returns the sender and the
Message-ID header as well, never the subject or snippet (OQ-44 e).

The drafts client is separate: it holds the compose grant, as
`inbox-agent-drafts`, and its one allowed request is `users.drafts.create`.
Anything else raises before a request is built, because the compose scope
would also allow sending (OQ-10).
"""

import base64
import json
import re
from email.message import EmailMessage
from email.utils import parseaddr
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from inbox_agent import settings
from inbox_agent.messages import FetchedEmail, parse_message

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
PAGE_SIZE = 100
# A poll covers about 90 minutes of mail. Far more than this means the query
# has gone wrong, so stop instead of reading the whole mailbox.
MAX_MESSAGES = 500
# Gmail message IDs are hex. Anything else never reaches a URL path.
_MESSAGE_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


@dataclass(frozen=True)
class GmailMessageRef:
    """IDs and the receipt time only: what the poller needs, and no email text."""

    message_id: str
    thread_id: str
    received_at: datetime  # Gmail's internalDate, for NFR-LAT-2


@dataclass(frozen=True)
class GmailMessageMeta:
    """A match of `search_gmail`: what drafting needs to reply in a thread,
    and no email text (OQ-44 e)."""

    message_id: str
    thread_id: str
    received_at: datetime
    sender: str  # the bare address, lowercase
    rfc822_message_id: str  # "" if the email has none


class GmailClient(Protocol):
    def search(self, query: str) -> list[GmailMessageRef]:
        """Every message matching the query, across all result pages."""
        ...

    def search_metadata(self, query: str, limit: int) -> list[GmailMessageMeta]:
        """The newest `limit` matches, with sender and Message-ID."""
        ...

    def fetch(self, message_id: str) -> FetchedEmail:
        """One message, read in full."""
        ...


class GmailError(RuntimeError):
    """Gmail or AgentCore refused. The message names the status, never email text."""


def _oauth_token(identity, workload: str, scope: str, grant: str) -> str:
    workload_token = identity.get_workload_access_token_for_user_id(
        workloadName=workload, userId=settings.OWNER_USER_ID
    )["workloadAccessToken"]
    # The same request the owner's consent run made, so the vault finds the token.
    response = identity.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=settings.GOOGLE_CREDENTIAL_PROVIDER,
        scopes=[scope],
        oauth2Flow="USER_FEDERATION",
        resourceOauth2ReturnUrl=settings.OAUTH_RETURN_URL,
        customParameters=settings.GOOGLE_OAUTH_PARAMETERS,
    )
    if not response.get("accessToken"):
        # Nobody can consent from a Lambda; the owner re-runs the spike.
        raise GmailError(
            f"no stored Gmail {grant} token: run `spikes/agentcore_identity_gmail.py --grant {grant}` on the Mac"
        )
    return response["accessToken"]


def _send(opener, request: urllib.request.Request) -> dict:
    try:
        with opener(request, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            status = json.loads(error.read() or b"{}").get("error", {}).get("status", "")
        except ValueError:
            status = ""
        raise GmailError(f"Gmail returned HTTP {error.code} {status}".strip()) from None


@dataclass
class AgentCoreGmailClient:
    identity: object  # boto3 bedrock-agentcore client
    opener: object = field(default=urllib.request.urlopen)

    def _token(self) -> str:
        return _oauth_token(self.identity, settings.POLLER_WORKLOAD_IDENTITY, settings.GMAIL_READ_SCOPE, "read")

    def _get(self, token: str, path: str, params: dict) -> dict:
        request = urllib.request.Request(
            f"{GMAIL_API}/{path}?{urllib.parse.urlencode(params, doseq=True)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return _send(self.opener, request)

    def search(self, query: str) -> list[GmailMessageRef]:
        token = self._token()
        found: list[tuple[str, str]] = []
        params = {"q": query, "maxResults": PAGE_SIZE}
        while True:
            page = self._get(token, "messages", params)
            found += [(m["id"], m["threadId"]) for m in page.get("messages", [])]
            if len(found) > MAX_MESSAGES:
                raise GmailError(f"more than {MAX_MESSAGES} messages match; the query is too wide")
            if not page.get("nextPageToken"):
                break
            params = {**params, "pageToken": page["nextPageToken"]}

        refs = []
        for message_id, thread_id in found:
            # `fields` keeps the snippet (email text) out of the response.
            meta = self._get(token, f"messages/{message_id}", {"format": "minimal", "fields": "id,internalDate"})
            received_at = datetime.fromtimestamp(int(meta["internalDate"]) / 1000, UTC)
            refs.append(GmailMessageRef(message_id=message_id, thread_id=thread_id, received_at=received_at))
        return refs


    def search_metadata(self, query: str, limit: int) -> list[GmailMessageMeta]:
        token = self._token()
        # Gmail lists newest first; one page of `limit` is all drafting needs.
        page = self._get(token, "messages", {"q": query, "maxResults": limit})
        found = []
        for listed in page.get("messages", [])[:limit]:
            # Only these two headers, and `fields` keeps the snippet out.
            meta = self._get(
                token,
                f"messages/{listed['id']}",
                {
                    "format": "metadata",
                    "metadataHeaders": ["From", "Message-ID"],
                    "fields": "id,threadId,internalDate,payload/headers",
                },
            )
            headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
            found.append(
                GmailMessageMeta(
                    message_id=meta["id"],
                    thread_id=meta["threadId"],
                    received_at=datetime.fromtimestamp(int(meta["internalDate"]) / 1000, UTC),
                    sender=parseaddr(headers.get("from", ""))[1].lower(),
                    rfc822_message_id=headers.get("message-id", "").strip(),
                )
            )
        return found

    def fetch(self, message_id: str) -> FetchedEmail:
        if not _MESSAGE_ID.match(message_id):
            raise GmailError("not a Gmail message ID")
        message = self._get(self._token(), f"messages/{message_id}", {"format": "raw", "fields": "id,internalDate,raw"})
        raw = base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4))
        received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC)
        return parse_message(raw, message_id=message_id, received_at=received_at)


def gmail_client() -> GmailClient:
    import boto3

    return AgentCoreGmailClient(boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION))


# The drafts-only client (TOOL-4)

_RFC822_ID = re.compile(r"^<[^<>\s]{1,250}>$")


def draft_message(*, to_address: str, subject: str, text: str, in_reply_to: str | None) -> bytes:
    """The follow-up as a MIME message. Header values with line breaks are
    refused by the email package, so no header can be smuggled in."""
    message = EmailMessage()
    if to_address:
        message["To"] = to_address
    message["Subject"] = subject
    if in_reply_to:
        if not _RFC822_ID.match(in_reply_to):
            raise GmailError("the stored Message-ID has the wrong form")
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(text)
    return message.as_bytes()


class DraftsOnlyGmail:
    """`users.drafts.create`, and nothing else (TOOL-4)."""

    ALLOWED = frozenset({("POST", "drafts")})

    def __init__(self, identity, opener=urllib.request.urlopen) -> None:
        self._identity = identity
        self._opener = opener

    def _request(self, method: str, path: str, body: dict) -> dict:
        # Checked before the token is fetched or a request is built.
        if (method, path) not in self.ALLOWED:
            raise GmailError("the drafts client may only create drafts")
        token = _oauth_token(
            self._identity, settings.DRAFTS_WORKLOAD_IDENTITY, settings.GMAIL_COMPOSE_SCOPE, "compose"
        )
        request = urllib.request.Request(
            f"{GMAIL_API}/{path}",
            data=json.dumps(body).encode(),
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        return _send(self._opener, request)

    def create_draft(self, raw: bytes, thread_id: str | None) -> str:
        message = {"raw": base64.urlsafe_b64encode(raw).decode()}
        if thread_id:
            if not _MESSAGE_ID.match(thread_id):
                raise GmailError("not a Gmail thread ID")
            message["threadId"] = thread_id
        return self._request("POST", "drafts", {"message": message})["id"]


def drafts_client() -> DraftsOnlyGmail:
    import boto3

    return DraftsOnlyGmail(boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION))
