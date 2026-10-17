"""WhatsApp Cloud API webhook (week 1 skeleton).

GET answers Meta's verify-token handshake (CHT-2 item 2). POST replies "pong"
to a "ping" from my own number and drops messages from anyone else (CHT-2
item 3, ALR-3). Signature checks, dedup and SQS hand-off arrive in week 3.

Logs carry counts and status codes only, never message text or phone numbers
(NFR-PRIV-3).
"""

import base64
import hmac
import json
import logging
import os
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GRAPH_API_URL = "https://graph.facebook.com/v23.0/{phone_number_id}/messages"


@dataclass(frozen=True)
class Settings:
    verify_token: str
    access_token: str
    app_secret: str
    phone_number_id: str
    owner_number: str


class SettingsError(RuntimeError):
    pass


_settings: Settings | None = None


def load_settings() -> Settings:
    """Read the SecureString parameters once per container."""
    global _settings
    if _settings is None:
        import boto3

        names = {
            "verify_token": os.environ["SSM_META_VERIFY_TOKEN"],
            "access_token": os.environ["SSM_META_ACCESS_TOKEN"],
            "app_secret": os.environ["SSM_META_APP_SECRET"],
            "phone_number_id": os.environ["SSM_META_PHONE_NUMBER_ID"],
            "owner_number": os.environ["SSM_OWNER_WHATSAPP_NUMBER"],
        }
        response = boto3.client("ssm").get_parameters(
            Names=list(names.values()), WithDecryption=True
        )
        if response["InvalidParameters"]:
            raise SettingsError(f"Missing SSM parameters: {sorted(response['InvalidParameters'])}")
        values = {p["Name"]: p["Value"] for p in response["Parameters"]}
        _settings = Settings(**{field: values[name] for field, name in names.items()})
    return _settings


def send_text(settings: Settings, body: str) -> None:
    """Send a free-form text to my number. The recipient is never an input (ALR-3)."""
    request = urllib.request.Request(
        GRAPH_API_URL.format(phone_number_id=settings.phone_number_id),
        data=json.dumps(
            {
                "messaging_product": "whatsapp",
                "to": settings.owner_number,
                "type": "text",
                "text": {"body": body},
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {settings.access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        logger.info("graph_api_status=%s", response.status)


def handler(event: dict, context: object) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    try:
        if method == "GET":
            return _verify(event, load_settings())
        if method == "POST":
            return _receive(event, load_settings())
    except SettingsError as error:
        logger.error("%s", error)
        return _response(500, "server misconfigured")
    return _response(405, "method not allowed")


def _verify(event: dict, settings: Settings) -> dict:
    params = event.get("queryStringParameters") or {}
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if not (mode and token and challenge):
        return _response(400, "bad request")
    if mode != "subscribe" or not hmac.compare_digest(token.encode(), settings.verify_token.encode()):
        logger.warning("verify_handshake=rejected")
        return _response(403, "forbidden")
    logger.info("verify_handshake=accepted")
    return _response(200, challenge)


def _receive(event: dict, settings: Settings) -> dict:
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        payload = json.loads(raw)
        messages = [
            message
            for entry in payload["entry"]
            for change in entry.get("changes", [])
            for message in change.get("value", {}).get("messages", [])
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return _response(400, "bad request")

    owner = _digits(settings.owner_number)
    dropped = pings = 0
    for message in messages:
        if _digits(str(message.get("from", ""))) != owner:
            dropped += 1
            continue
        if message.get("type") == "text" and message.get("text", {}).get("body", "").strip().lower() == "ping":
            pings += 1
            send_text(settings, "pong")

    logger.info("messages=%d dropped_non_owner=%d pings=%d", len(messages), dropped, pings)
    # Meta retries anything that is not a 200, so status updates and
    # messages the skeleton ignores are acknowledged too.
    return _response(200, "ok")


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def _response(status: int, body: str) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": body}
