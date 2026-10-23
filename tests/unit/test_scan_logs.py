"""The log scan (NFR-PRIV-3, OQ-45 h) over fakes: values are searched as
exact phrases, matches are counted and never printed, and an empty week says
so. Subjects and senders are fictional."""

import json
from datetime import UTC, datetime, timedelta

from inbox_agent import scan_logs
from tests.fakes import FakeTable

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SUBJECT = "Mülakat daveti: Backend Geliştirici"
SENDER = "İK Ekibi <ik@kuzey.example.com>"


class FakeGmail:
    def __init__(self, headers):
        self.by_id = headers
        self.asked = []

    def token(self):
        return "token"

    def headers(self, message_id, token):
        self.asked.append(message_id)
        if message_id not in self.by_id:
            raise RuntimeError("Gmail returned HTTP 404")
        return self.by_id[message_id]


class FakeLogs:
    """Log groups with lines; a filter pattern of OR-ed quoted phrases."""

    def __init__(self, groups):
        self.groups = groups
        self.patterns = []

    def get_paginator(self, name):
        groups = self.groups

        class Paginator:
            def paginate(self):
                yield {"logGroups": [{"logGroupName": g} for g in groups]}

        return Paginator()

    def filter_log_events(self, logGroupName, filterPattern, startTime, **kwargs):
        self.patterns.append(filterPattern)
        phrases = _phrases(filterPattern)
        lines = self.groups[logGroupName]
        return {"events": [{"message": line} for line in lines if any(phrase in line for phrase in phrases)]}


def _phrases(pattern):
    """The phrases of `?"a" ?"b"`, unescaped one level."""
    out, i = [], 0
    while (start := pattern.find('?"', i)) >= 0:
        j, buf = start + 2, []
        while pattern[j] != '"':
            if pattern[j] == "\\":
                j += 1
            buf.append(pattern[j])
            j += 1
        out.append("".join(buf))
        i = j + 1
    return out


def table(*items):
    t = FakeTable("message_id")
    for item in items:
        t.put_item(Item=item)
    return t


def epoch(moment):
    return int(moment.timestamp())


def seeded():
    return table(
        {"message_id": "18c0ffee", "logged_at": epoch(NOW - timedelta(days=2))},
        {"message_id": "18c0ffef", "logged_at": epoch(NOW - timedelta(days=1))},
        {"message_id": "18cold00", "logged_at": epoch(NOW - timedelta(days=9))},
        {"message_id": "#poll-state", "last_success": epoch(NOW)},
        {"message_id": "#drafts-ready-2026-10-21", "logged_at": epoch(NOW)},
    )


def gmail():
    return FakeGmail({"18c0ffee": {"subject": SUBJECT, "from": SENDER}, "18c0ffef": {"subject": "Re: Hi", "from": "x@y.example.com"}})


def test_only_this_weeks_emails_are_read_and_bookkeeping_is_skipped():
    assert scan_logs.processed_ids(seeded(), NOW - timedelta(days=7)) == ["18c0ffee", "18c0ffef"]


def test_values_are_the_subject_address_and_name_as_plain_ascii_fragments():
    values = scan_logs.values_for({"subject": "  Mülakat   daveti: Backend Geliştirici ", "from": SENDER})
    assert values == [SUBJECT, "ik@kuzey.example.com", "İK Ekibi"]
    forms, skipped = scan_logs.search_forms(values + ["Re: Hi", 'Say "hello there" \\ again'])
    # Turkish letters split a value, as json.dumps escapes them in a log line.
    assert forms == ["lakat daveti: Backend Geli", "ik@kuzey.example.com", "hello there"]
    # "M", "tirici", "K Ekibi", "Re: Hi", "Say", "again" are too short.
    assert skipped == 6
    assert all('"' not in f and "\\" not in f for f in forms)
    # Each fragment appears as is in the escaped form of the value, too.
    assert "lakat daveti: Backend Geli" in json.dumps(SUBJECT)


def test_each_fragment_is_an_exact_quoted_phrase():
    assert scan_logs.term("lakat daveti") == '"lakat daveti"'
    patterns = list(scan_logs.patterns(["a" * 600, "b" * 600, "c" * 2000]))
    assert all(len(p) <= scan_logs.PATTERN_LIMIT for p in patterns) and len(patterns) == 3
    assert patterns[0].startswith('?"aaa')


def test_a_clean_week_counts_zero_everywhere(capsys):
    logs = FakeLogs({"/aws/lambda/poll": ['{"event": "poll_done", "found": 2}'], "/aws/runtime": ['{"event": "batch_done"}']})
    result = scan_logs.scan(table=seeded(), gmail=gmail(), logs=logs, now=NOW)
    assert result.clean and result.emails == 2 and result.skipped_short == 4
    assert result.counts == {"/aws/lambda/poll": 0, "/aws/runtime": 0}
    assert "CLEAN" in scan_logs.report(result)[-1]


def test_a_match_is_counted_and_never_printed():
    leaked = json.dumps({"event": "oops", "subject": SUBJECT})  # escaped, as json.dumps writes it
    logs = FakeLogs({"/aws/lambda/poll": ["fine"], "/aws/runtime": [leaked, "from ik@kuzey.example.com"]})
    result = scan_logs.scan(table=seeded(), gmail=gmail(), logs=logs, now=NOW)
    assert not result.clean and result.counts["/aws/runtime"] == 2 and result.counts["/aws/lambda/poll"] == 0
    printed = "\n".join(scan_logs.report(result))
    assert "MATCHES FOUND" in printed
    assert SUBJECT not in printed and "kuzey" not in printed and "İK" not in printed


def test_an_email_gmail_no_longer_has_is_counted():
    result = scan_logs.scan(table=seeded(), gmail=FakeGmail({}), logs=FakeLogs({"/g": []}), now=NOW)
    assert result.unreadable == 2 and result.values == 0


def test_an_empty_week_says_so():
    result = scan_logs.scan(table=table(), gmail=gmail(), logs=FakeLogs({"/g": ["x"]}), now=NOW)
    assert result.emails == 0 and result.clean
    assert any("no processed emails this week" in line for line in scan_logs.report(result))
