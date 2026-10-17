import base64
import json

import pytest

import whatsapp_webhook as webhook

OWNER = "905550000000"  # fictional test number
SETTINGS = webhook.Settings(
    verify_token="test-verify-token",
    access_token="test-access-token",
    app_secret="test-app-secret",
    phone_number_id="123456789",
    owner_number=f"+{OWNER}",
)


@pytest.fixture
def sent(monkeypatch):
    """Patch SSM and the Graph API; collect what would be sent."""
    outbox: list[str] = []
    monkeypatch.setattr(webhook, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(webhook, "send_text", lambda settings, body: outbox.append(body))
    return outbox


def get_event(params):
    return {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": params}


def post_event(body, *, base64_encoded=False):
    if base64_encoded:
        body = base64.b64encode(body.encode()).decode()
    return {"requestContext": {"http": {"method": "POST"}}, "body": body, "isBase64Encoded": base64_encoded}


def whatsapp_payload(*messages):
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "1", "changes": [{"field": "messages", "value": {"messages": list(messages)}}]}],
        }
    )


def text_message(text, sender=OWNER):
    return {"from": sender, "id": "wamid.test", "type": "text", "text": {"body": text}}


# Handshake


def test_handshake_returns_challenge(sent):
    response = webhook.handler(
        get_event({"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "1158201444"}),
        None,
    )
    assert response["statusCode"] == 200
    assert response["body"] == "1158201444"


def test_handshake_wrong_token_is_forbidden(sent):
    response = webhook.handler(
        get_event({"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1158201444"}), None
    )
    assert response["statusCode"] == 403
    assert "1158201444" not in response["body"]


def test_handshake_wrong_mode_is_forbidden(sent):
    response = webhook.handler(
        get_event({"hub.mode": "unsubscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "1"}), None
    )
    assert response["statusCode"] == 403


@pytest.mark.parametrize("params", [None, {}, {"hub.mode": "subscribe", "hub.verify_token": "test-verify-token"}])
def test_handshake_missing_parameters_is_bad_request(sent, params):
    assert webhook.handler(get_event(params), None)["statusCode"] == 400


# Ping / pong


@pytest.mark.parametrize("text", ["ping", "Ping", "  PING \n"])
def test_ping_from_owner_gets_pong(sent, text):
    response = webhook.handler(post_event(whatsapp_payload(text_message(text))), None)
    assert response["statusCode"] == 200
    assert sent == ["pong"]


def test_base64_body_is_decoded(sent):
    response = webhook.handler(post_event(whatsapp_payload(text_message("ping")), base64_encoded=True), None)
    assert response["statusCode"] == 200
    assert sent == ["pong"]


def test_other_text_gets_no_reply(sent):
    response = webhook.handler(post_event(whatsapp_payload(text_message("What is open with Brightpath?"))), None)
    assert response["statusCode"] == 200
    assert sent == []


def test_ping_from_stranger_is_dropped(sent):
    response = webhook.handler(post_event(whatsapp_payload(text_message("ping", sender="905551112233"))), None)
    assert response["statusCode"] == 200
    assert sent == []


def test_status_update_without_messages_is_acknowledged(sent):
    payload = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]})
    response = webhook.handler(post_event(payload), None)
    assert response["statusCode"] == 200
    assert sent == []


def test_pong_goes_to_owner_number_only():
    """The recipient comes from settings, never from the incoming message (ALR-3)."""
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["auth"] = request.get_header("Authorization")
        return FakeResponse()

    original = webhook.urllib.request.urlopen
    webhook.urllib.request.urlopen = fake_urlopen
    try:
        webhook.send_text(SETTINGS, "pong")
    finally:
        webhook.urllib.request.urlopen = original

    assert captured["url"].endswith("/123456789/messages")
    assert captured["payload"]["to"] == SETTINGS.owner_number
    assert captured["payload"]["text"] == {"body": "pong"}
    assert captured["auth"] == "Bearer test-access-token"


# Bad requests


@pytest.mark.parametrize(
    "body",
    ["", "not json", "[]", json.dumps({"object": "whatsapp_business_account"}), json.dumps({"entry": "x"})],
)
def test_malformed_post_is_bad_request(sent, body):
    response = webhook.handler(post_event(body), None)
    assert response["statusCode"] == 400
    assert sent == []


def test_unsupported_method(sent):
    assert webhook.handler({"requestContext": {"http": {"method": "PUT"}}}, None)["statusCode"] == 405


def test_missing_ssm_parameters_return_500(monkeypatch):
    def missing():
        raise webhook.SettingsError("Missing SSM parameters: ['/inbox-agent/meta/verify-token']")

    monkeypatch.setattr(webhook, "load_settings", missing)
    response = webhook.handler(get_event({"hub.mode": "subscribe"}), None)
    assert response["statusCode"] == 500
