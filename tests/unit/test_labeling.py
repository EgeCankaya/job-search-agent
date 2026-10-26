"""EV-1's labeling tools (OQ-47 (d)): the set lives outside the repo, the
fetch follows the §12 sampling rule and prints counts only, labels are
validated, and the labeling page saves what I enter. Emails are fictional;
no AWS, no Google."""

import base64
import io
import json
import stat
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from eval import fetch_labeling
from eval.fetch_labeling import RawReader, fetch_window, manual_query, window_query, window_start
from eval.labeled_set import ROOT, LabeledSet, LabelError, data_dir, make_label
from inbox_agent import settings
from inbox_agent.gmail import GmailMessageRef

RECEIVED = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SUBJECT = "Mülakat daveti: Backend Geliştirici"


def raw_email(subject=SUBJECT, body="Merhaba, 3 Kasım 14:00 için mülakata davet ediyoruz.") -> bytes:
    return (
        "From: =?utf-8?q?=C4=B0K?= <ik@kuzey.example.com>\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


@pytest.fixture
def store(tmp_path):
    return LabeledSet(tmp_path / "set").ensure()


def add(store, gmail_id="19a0000001", found_by="query"):
    return store.add_email(
        raw_email(), gmail_id=gmail_id, thread_id=f"t{gmail_id}", received_at=RECEIVED,
        found_by=found_by, window="2026-09-01..2026-10-01", now=NOW,
    )


def label(eval_id, **changes):
    fields = dict(
        eval_id=eval_id, email_class="interview_invite", asks_by_date=True, company=" Kuzey  Yazılım ",
        role="Backend Geliştirici", key_date="2026-11-03T14:00", language="tr", note="", now=NOW,
    )
    return make_label(**{**fields, **changes})


# Where the set lives


def test_the_set_is_refused_inside_the_repo(tmp_path):
    with pytest.raises(ValueError, match="outside the repo"):
        data_dir({"EVAL_DATA_DIR": str(ROOT / "data" / "eval")})
    with pytest.raises(ValueError):
        data_dir({"EVAL_DATA_DIR": str(ROOT)})
    assert data_dir({"EVAL_DATA_DIR": str(tmp_path)}) == tmp_path.resolve()
    assert data_dir({}) == (Path.home() / "inbox-agent-eval").resolve()


def test_emails_are_stored_once_under_eval_ids_and_only_I_can_read_them(store):
    first, again, second = add(store), add(store), add(store, gmail_id="19a0000002", found_by="manual")
    assert (first, again, second) == ("ev1-0001", "ev1-0001", "ev1-0002")
    index = store.index()
    assert index["ev1-0002"].found_by == "manual"
    assert store.email("ev1-0001").header.subject == SUBJECT
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.emails / "ev1-0001.eml").stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        add(store, gmail_id="19a0000003", found_by="model")


# Labels


def test_a_label_is_cleaned_and_the_last_one_wins(store):
    eval_id = add(store)
    store.save_label(label(eval_id))
    store.save_label(label(eval_id, email_class="assessment", role="", key_date=""))
    saved = store.labels()[eval_id]
    assert (saved.email_class, saved.company, saved.role, saved.key_date) == ("assessment", "Kuzey Yazılım", None, None)
    assert store.progress() == {
        "emails": 1, "labeled": 1, "by_found_by": {"query": 1, "manual": 0}, "by_class": {"assessment": {"tr": 1}},
    }


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"email_class": "spam"}, "email_class"),
        ({"key_date": "3 Kasım"}, "key_date"),
        ({"key_date": "2026-02-30"}, "key_date"),
        ({"key_date": "2026-11-03 14:00"}, "key_date"),
        ({"language": "de"}, "language"),
        ({"company": "x" * 101}, "company"),
        ({"note": "x" * 501}, "note"),
    ],
)
def test_a_bad_label_is_refused_without_quoting_it(changes, field):
    with pytest.raises(LabelError) as error:
        label("ev1-0001", **changes)
    assert str(error.value).startswith(field)
    assert all(str(value) not in str(error.value) for value in changes.values())


def test_a_label_for_an_email_not_in_the_set_is_refused(store):
    with pytest.raises(LabelError, match="not in the set"):
        store.save_label(label("ev1-0042"))


# The fetch window and its queries


def test_the_window_query_is_the_poll_query_with_the_window_in_place_of_after():
    after, before = window_start(date(2026, 9, 1)), window_start(date(2026, 10, 1))
    rows = [{"company_domains": ["kuzey.example.com"], "recruiter_senders": ["ayse@recruit.example.com"]}]
    query = window_query(rows, after, before)
    # Midnight in Istanbul (UTC+3), with no 1-hour poll overlap.
    assert f"after:{int(datetime(2026, 8, 31, 21, 0, tzinfo=UTC).timestamp())}" in query
    assert f"before:{int(datetime(2026, 9, 30, 21, 0, tzinfo=UTC).timestamp())}" in query
    assert "from:kuzey.example.com" in query and "from:ayse@recruit.example.com" in query
    assert settings.OWN_MAIL_EXCLUSION in query
    assert query.endswith(f"-from:{settings.ALERT_SENDER}")


@pytest.mark.parametrize("name", ["eval manual", "x OR label:inbox", 'a"b', "-x", ""])
def test_the_manual_label_cant_widen_the_search(name):
    with pytest.raises(ValueError):
        manual_query(name, window_start(date(2026, 9, 1)), window_start(date(2026, 10, 1)))
    assert manual_query("eval-manual", window_start(date(2026, 9, 1)), window_start(date(2026, 10, 1))).startswith(
        "label:eval-manual after:"
    )


class FakeReader:
    def __init__(self, query_ids, manual_ids):
        self.results = {"query": query_ids, "manual": manual_ids}
        self.fetched = []

    def search(self, query):
        ids = self.results["manual" if query.startswith("label:") else "query"]
        return [GmailMessageRef(message_id=i, thread_id=f"t{i}", received_at=RECEIVED) for i in ids]

    def fetch_raw(self, message_id):
        self.fetched.append(message_id)
        return raw_email(), f"t{message_id}", RECEIVED


def test_the_query_finds_first_and_my_manual_finds_count_only_when_the_query_missed_them(store):
    reader = FakeReader(query_ids=["19a01", "19a02"], manual_ids=["19a02", "19a03"])
    counts = fetch_window(store, reader, query="(from:x.example.com)", manual="label:eval-manual", window="w", now=NOW)
    assert (counts.query_matched, counts.query_new) == (2, 2)
    assert (counts.manual_matched, counts.manual_new, counts.manual_also_query) == (2, 1, 1)
    found = {entry.gmail_id: entry.found_by for entry in store.index().values()}
    assert found == {"19a01": "query", "19a02": "query", "19a03": "manual"}

    # A second run of the same window fetches nothing again.
    reader.fetched.clear()
    again = fetch_window(store, reader, query="(from:x.example.com)", manual="label:eval-manual", window="w", now=NOW)
    assert (again.query_new, again.manual_new, reader.fetched) == (0, 0, [])


def test_a_dry_run_counts_and_saves_nothing():
    reader = FakeReader(query_ids=["19a01"], manual_ids=["19a03"])
    counts = fetch_window(None, reader, query="q", manual="label:eval-manual", window="w")
    assert (counts.query_new, counts.manual_new, reader.fetched) == (1, 1, [])


def test_the_output_is_counts_only(store):
    reader = FakeReader(query_ids=["19a01"], manual_ids=[])
    lines = fetch_window(store, reader, query="q", manual="label:eval-manual", window="w").lines(store)
    text = "\n".join(lines)
    assert "19a01" not in text and "ev1-" not in text and "Mülakat" not in text and "kuzey" not in text
    assert "1 emails (1 query, 0 manual), 0 labeled" in text


class FakeIdentity:
    def __init__(self):
        self.calls = 0

    def get_workload_access_token_for_user_id(self, **kwargs):
        self.calls += 1
        return {"workloadAccessToken": "workload-token"}

    def get_resource_oauth2_token(self, **kwargs):
        return {"accessToken": "google-token"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_the_raw_reader_returns_gmails_bytes_and_keeps_one_token_per_run():
    requests = []

    def opener(request, timeout):
        requests.append(request)
        body = {
            "id": "19a01", "threadId": "t19a01", "internalDate": str(int(RECEIVED.timestamp() * 1000)),
            "raw": base64.urlsafe_b64encode(raw_email()).decode().rstrip("="),
        }
        return FakeResponse(json.dumps(body).encode())

    identity = FakeIdentity()
    reader = RawReader(identity, opener=opener)
    first = reader.fetch_raw("19a01")
    reader.fetch_raw("19a01")
    assert first == (raw_email(), "t19a01", RECEIVED)
    assert identity.calls == 1
    params = urllib.parse.parse_qs(urllib.parse.urlparse(requests[0].full_url).query)
    assert params["format"] == ["raw"] and params["fields"] == ["id,threadId,internalDate,raw"]
    with pytest.raises(fetch_labeling.GmailError):
        reader.fetch_raw("../labels")


# The labeling page


@pytest.fixture
def page(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("EVAL_DATA_DIR", str(tmp_path / "set"))
    return AppTest.from_file(str(ROOT / "eval" / "label_app.py"), default_timeout=30)


def test_the_page_says_when_the_set_is_empty(page):
    page.run()
    assert not page.exception
    assert "fetch_labeling.py" in page.info[0].value


def test_the_page_shows_the_trimmed_email_and_saves_my_label(page, tmp_path):
    store = LabeledSet(tmp_path / "set").ensure()
    first, second = add(store), add(store, gmail_id="19a0000002")
    page.run()
    assert not page.exception
    assert page.selectbox[0].value == first
    assert any("3 Kasım 14:00" in text.value for text in page.text)
    # Nothing is chosen for me: no class and no language until I pick them.
    assert page.radio[0].value is None and page.radio[1].value is None

    page.button[0].click().run()
    assert "choose a class and a language" in page.error[0].value
    assert store.labels() == {}

    page.radio[0].set_value("interview_invite")
    page.radio[1].set_value("tr")
    page.text_input[0].set_value("Kuzey Yazılım")
    page.text_input[2].set_value("2026-11-03T14:00")
    page.button[0].click().run()
    assert not page.exception
    saved = store.labels()[first]
    assert (saved.email_class, saved.company, saved.role, saved.key_date, saved.language) == (
        "interview_invite", "Kuzey Yazılım", None, "2026-11-03T14:00", "tr",
    )
    # The next unlabeled email comes up.
    assert page.selectbox[0].value == second
