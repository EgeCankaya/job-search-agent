"""The Gmail interface the poller uses (ING-1), and its real client (ING-4).

The real client gets a `gmail.readonly` token from AgentCore Identity for the
owner, as the poller's workload identity, then calls the Gmail REST API with
the standard library. It asks Gmail only for IDs and the receipt time, and
never for the snippet or any other email text (NFR-PRIV-1, NFR-PRIV-3).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from inbox_agent import settings

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
PAGE_SIZE = 100
# A poll covers about 90 minutes of mail. Far more than this means the query
# has gone wrong, so stop instead of reading the whole mailbox.
MAX_MESSAGES = 500


@dataclass(frozen=True)
class GmailMessageRef:
    """IDs and the receipt time only: what the poller needs, and no email text."""

    message_id: str
    thread_id: str
    received_at: datetime  # Gmail's internalDate, for NFR-LAT-2


class GmailClient(Protocol):
    def search(self, query: str) -> list[GmailMessageRef]:
        """Every message matching the query, across all result pages."""
        ...


class GmailError(RuntimeError):
    """Gmail or AgentCore refused. The message names the status, never email text."""


@dataclass
class AgentCoreGmailClient:
    identity: object  # boto3 bedrock-agentcore client
    opener: object = field(default=urllib.request.urlopen)

    def _token(self) -> str:
        workload_token = self.identity.get_workload_access_token_for_user_id(
            workloadName=settings.POLLER_WORKLOAD_IDENTITY, userId=settings.OWNER_USER_ID
        )["workloadAccessToken"]
        # The same request the owner's consent run made, so the vault finds the token.
        response = self.identity.get_resource_oauth2_token(
            workloadIdentityToken=workload_token,
            resourceCredentialProviderName=settings.GOOGLE_CREDENTIAL_PROVIDER,
            scopes=[settings.GMAIL_READ_SCOPE],
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=settings.OAUTH_RETURN_URL,
            customParameters=settings.GOOGLE_OAUTH_PARAMETERS,
        )
        if not response.get("accessToken"):
            # Nobody can consent from a Lambda; the owner re-runs the spike.
            raise GmailError(
                "no stored Gmail read token: run `spikes/agentcore_identity_gmail.py --grant read` on the Mac"
            )
        return response["accessToken"]

    def _get(self, token: str, path: str, params: dict) -> dict:
        request = urllib.request.Request(
            f"{GMAIL_API}/{path}?{urllib.parse.urlencode(params)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with self.opener(request, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                status = json.loads(error.read() or b"{}").get("error", {}).get("status", "")
            except ValueError:
                status = ""
            raise GmailError(f"Gmail returned HTTP {error.code} {status}".strip()) from None

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


def gmail_client() -> GmailClient:
    import boto3

    return AgentCoreGmailClient(boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION))
