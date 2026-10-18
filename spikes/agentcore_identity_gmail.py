"""Week 2 spike (ING-4, PRD §16a): can AgentCore Identity hold two Google
grants for one user, and hand out refreshed tokens with nobody signed in?

Two workload identities split the grants:
- `inbox-agent-poller` gets only `gmail.readonly` (the poller and read tools);
- `inbox-agent-drafts` gets only `gmail.compose` (the draft tool).

The first run for a grant prints a Google sign-in link. After consent,
AgentCore redirects the browser to a callback server on 127.0.0.1, which
checks that the session is the one this run started, then completes the
session binding (CompleteResourceTokenAuth). Later runs get a token straight
from the token vault, refreshed by AgentCore: that is the day-8 check.

The script prints counts, message IDs and HTTP statuses only, never tokens
or email text (NFR-PRIV-3). It never sends mail and creates no draft with
the compose grant.

    uv run python spikes/agentcore_identity_gmail.py --grant read
"""

import argparse
import base64
import json
import sys
from pathlib import Path
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inbox_agent import settings  # noqa: E402
from inbox_agent.query import build_query  # noqa: E402

PROVIDER_NAME = "inbox-agent-google"
# One fixed user for the whole project: the owner. Not personal data.
OWNER_USER_ID = "owner"
CALLBACK_HOST, CALLBACK_PORT, CALLBACK_PATH = "127.0.0.1", 9090, "/oauth2/callback"
RETURN_URL = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
CONSENT_TIMEOUT_SECONDS = 600  # an authorization URL is valid for 10 minutes

GRANTS = {
    "read": ("inbox-agent-poller", "https://www.googleapis.com/auth/gmail.readonly"),
    "compose": ("inbox-agent-drafts", "https://www.googleapis.com/auth/gmail.compose"),
}
# Google returns a refresh token only for offline access; "consent" makes it
# return one again on a repeated sign-in.
GOOGLE_PARAMETERS = {"access_type": "offline", "prompt": "consent"}

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


class SpikeError(RuntimeError):
    pass


def get_access_token(
    identity,
    *,
    workload: str,
    scope: str,
    wait_for_session: Callable[[], str],
    show_url: Callable[[str], None],
    force: bool = False,
) -> str:
    """A Google access token for the owner, from the token vault or after consent."""
    workload_token = identity.get_workload_access_token_for_user_id(
        workloadName=workload, userId=OWNER_USER_ID
    )["workloadAccessToken"]
    request = {
        "workloadIdentityToken": workload_token,
        "resourceCredentialProviderName": PROVIDER_NAME,
        "scopes": [scope],
        "oauth2Flow": "USER_FEDERATION",
        "resourceOauth2ReturnUrl": RETURN_URL,
        "customParameters": GOOGLE_PARAMETERS,
    }
    first = identity.get_resource_oauth2_token(**request, forceAuthentication=force)
    if first.get("accessToken"):
        return first["accessToken"]
    if not first.get("authorizationUrl") or not first.get("sessionUri"):
        raise SpikeError(f"no token and no authorization URL (sessionStatus={first.get('sessionStatus')})")

    show_url(first["authorizationUrl"])
    session = wait_for_session()
    # Session binding: complete only the session this run started.
    if session != first["sessionUri"]:
        raise SpikeError("the callback carried a different session; not completing it")
    identity.complete_resource_token_auth(userIdentifier={"userId": OWNER_USER_ID}, sessionUri=session)

    second = identity.get_resource_oauth2_token(**request)
    if not second.get("accessToken"):
        raise SpikeError(f"consent completed but no token came back (sessionStatus={second.get('sessionStatus')})")
    return second["accessToken"]


def wait_for_callback(timeout: int = CONSENT_TIMEOUT_SECONDS) -> str:
    """Serve one request on 127.0.0.1 and return its session_id."""
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            session = urllib.parse.parse_qs(url.query).get("session_id", [""])[0]
            ok = url.path == CALLBACK_PATH and bool(session)
            if ok:
                received["session"] = session
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Inbox agent: done, you can close this tab." if ok else b"Missing session_id.")

        def log_message(self, *args):  # keep the session ID out of the terminal
            pass

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    server.timeout = 5
    deadline = datetime.now(UTC) + timedelta(seconds=timeout)
    try:
        while "session" not in received and datetime.now(UTC) < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "session" not in received:
        raise SpikeError("no callback within 10 minutes; run the script again")
    return received["session"]


def _gmail(token: str, path: str, *, data: dict | None = None, opener=urllib.request.urlopen) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{GMAIL}/{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with opener(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}


def _reason(body: dict) -> str:
    """Google's error status and message. They describe the request, never the mailbox."""
    error = body.get("error", {}) if isinstance(body, dict) else {}
    return " ".join(part for part in (error.get("status"), error.get("message")) if part) or "no details"


def list_matching_ids(token: str, *, days: int = 7, opener=urllib.request.urlopen) -> list[str]:
    """IDs of the last `days` of mail matching the ING-1 query (settings only, no tracker)."""
    query = build_query([], datetime.now(UTC) - timedelta(days=days)).to_gmail()
    status, body = _gmail(token, "messages?" + urllib.parse.urlencode({"q": query, "maxResults": 20}), opener=opener)
    if status != 200:
        raise SpikeError(f"Gmail search returned HTTP {status}: {_reason(body)}")
    return [message["id"] for message in body.get("messages", [])]


def read_token_draft_status(token: str, opener=urllib.request.urlopen) -> int:
    """UAC-ING-4: the read token must be refused when it tries to create a draft."""
    raw = base64.urlsafe_b64encode(b"Subject: inbox-agent scope check\r\n\r\n").decode()
    status, _ = _gmail(token, "drafts", data={"message": {"raw": raw}}, opener=opener)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentCore Identity + Gmail spike (ING-4)")
    parser.add_argument("--grant", choices=sorted(GRANTS), required=True)
    parser.add_argument("--force", action="store_true", help="sign in again even if a token is stored")
    parser.add_argument(
        "--check-draft-refused", action="store_true", help="with --grant read: try a draft and expect HTTP 403"
    )
    args = parser.parse_args(argv)

    import boto3

    identity = boto3.client("bedrock-agentcore", region_name=settings.AWS_REGION)
    workload, scope = GRANTS[args.grant]

    def show_url(url: str) -> None:
        print(f"\nOpen this link, sign in with your Gmail account and allow access:\n\n{url}\n")
        print(f"Waiting up to 10 minutes for the redirect to {RETURN_URL} ...")

    # Start listening before the link is shown, so a fast consent can't miss it.
    session_box: dict[str, object] = {}
    listener = threading.Thread(target=lambda: session_box.update(value=_safe(wait_for_callback)), daemon=True)

    def wait() -> str:
        listener.join()
        value = session_box.get("value")
        if isinstance(value, Exception):
            raise value
        return value

    listener.start()
    try:
        token = get_access_token(
            identity, workload=workload, scope=scope, wait_for_session=wait, show_url=show_url, force=args.force
        )
    except SpikeError as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    print(f"Got a {args.grant} token for workload {workload} (not printed).")

    if args.grant == "read":
        try:
            ids = list_matching_ids(token)
        except SpikeError as error:
            print(f"Failed: {error}", file=sys.stderr)
            return 1
        print(f"Gmail search with the ING-1 query, last 7 days: {len(ids)} messages")
        for message_id in ids:
            print(f"  {message_id}")
        if args.check_draft_refused:
            status = read_token_draft_status(token)
            verdict = "refused, as required" if status == 403 else "NOT REFUSED: delete the test draft and report this"
            print(f"Draft attempt with the read token: HTTP {status}, {verdict}")
            if status != 403:
                return 1
    return 0


def _safe(function):
    try:
        return function()
    except Exception as error:  # handed to the main thread
        return error


if __name__ == "__main__":
    sys.exit(main())
