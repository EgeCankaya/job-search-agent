"""TRK-3 import (UAC-TRK-3). Fictional companies and example.com addresses only."""

import subprocess
from datetime import date
from pathlib import Path

import pytest

from inbox_agent import import_applications as importer
from inbox_agent.import_applications import CsvError, import_rows, parse_aliases, parse_csv
from inbox_agent.tracker import TrackerTable
from tests.fakes import FakeTable

REPO = Path(__file__).resolve().parents[2]

THREE_ROWS = """company,role,applied date,status,company domain,contact address
Kuzey Yazılım A.Ş.,Backend Engineer,2026-09-20,applied,kuzey.example.com,ik@kuzey.example.com
Example Robotics GmbH,Data Engineer,2026-09-25,interviewing,,
Güney Veri Ltd. Şti.,Yazılım Mühendisi,01.10.2026,applied,https://www.guney.example.org/careers,
"""


def tracker():
    return TrackerTable(FakeTable("company_key", "role_key"))


def test_importing_the_same_three_rows_twice_leaves_three_rows():
    table = tracker()
    first = import_rows(parse_csv(THREE_ROWS), table)
    second = import_rows(parse_csv(THREE_ROWS), table)
    assert (first.created, first.updated, first.unchanged) == (3, 0, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 3)
    assert len(table.rows()) == 3


def test_reimport_after_renaming_matches_the_same_row():
    table = tracker()
    import_rows(parse_csv(THREE_ROWS), table)
    renamed = THREE_ROWS.replace("Kuzey Yazılım A.Ş.", "KUZEY YAZILIM").replace("Backend Engineer", "backend engineer")
    result = import_rows(parse_csv(renamed), table)
    assert result.created == 0
    assert len(table.rows()) == 3


def test_row_fields():
    table = tracker()
    import_rows(parse_csv(THREE_ROWS), table)
    kuzey = table.get("kuzey yazilim", "backend engineer")
    assert kuzey == {
        "company_key": "kuzey yazilim",
        "role_key": "backend engineer",
        "company": "Kuzey Yazılım A.Ş.",
        "role": "Backend Engineer",
        "status": "applied",
        "applied_date": "2026-09-20",
        "last_contact": "2026-09-20",
        "company_domains": ["kuzey.example.com"],
        "recruiter_senders": ["ik@kuzey.example.com"],
    }
    robotics = table.get("example robotics", "data engineer")
    assert "company_domains" not in robotics and "recruiter_senders" not in robotics
    guney = table.get("guney veri", "yazilim muhendisi")
    assert guney["applied_date"] == "2026-10-01"
    assert guney["company_domains"] == ["guney.example.org"]


def test_reimport_keeps_fields_the_agent_added():
    table = tracker()
    import_rows(parse_csv(THREE_ROWS), table)
    row = table.get("kuzey yazilim", "backend engineer")
    row.update(last_contact="2026-10-12", source_message_ids=["18c0ffee"], company_domains=["kuzey.example.net"])
    table.put(row)

    result = import_rows(parse_csv(THREE_ROWS), table)
    assert result.updated == 1
    row = table.get("kuzey yazilim", "backend engineer")
    assert row["last_contact"] == "2026-10-12"
    assert row["source_message_ids"] == ["18c0ffee"]
    assert row["company_domains"] == ["kuzey.example.com", "kuzey.example.net"]


def test_semicolon_csv_with_bom_header_names():
    text = "﻿Company;Role;Applied_Date;Status\nKuzey Yazılım;QA Engineer;2026-09-01;Recruiter Contact\n"
    [row] = parse_csv(text.lstrip("﻿"))
    assert row.status == "recruiter_contact"
    assert row.applied_date == date(2026, 9, 1)


def test_quoted_field_with_comma():
    text = 'company,role,applied_date,status\n"Kuzey Yazılım, Ankara",Backend Engineer,2026-09-01,applied\n'
    [row] = parse_csv(text)
    assert row.company == "Kuzey Yazılım, Ankara"


def test_missing_columns_are_reported():
    with pytest.raises(CsvError, match="applied_date, status"):
        parse_csv("company,role\nKuzey Yazılım,Backend Engineer\n")


def test_every_bad_row_is_reported_and_nothing_is_written():
    text = """company,role,applied_date,status,company_domain,contact_address
Kuzey Yazılım,Backend Engineer,2026-13-01,applied,,
,Data Engineer,2026-09-01,hired,not a domain,not-an-address
KUZEY YAZILIM A.Ş.,backend engineer,2026-09-02,applied,,
"""
    with pytest.raises(CsvError) as error:
        parse_csv(text)
    message = str(error.value)
    assert "line 2: applied_date" in message
    assert "line 3: company is empty" in message
    assert "line 3: status must be one of" in message
    assert "line 3: company_domain: not a domain" in message
    assert "line 3: contact_address: not an email address" in message
    assert "line 4: same application as line 2" in message


def test_blank_lines_are_skipped():
    text = "company,role,applied_date,status\n\n,,,\nKuzey Yazılım,Backend Engineer,2026-09-01,applied\n"
    assert len(parse_csv(text)) == 1


def test_main_prints_counts_only(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "applications.csv"
    csv_path.write_text(THREE_ROWS, encoding="utf-8")
    table = FakeTable("company_key", "role_key")
    monkeypatch.setattr(importer, "_dynamodb_table", lambda name: table)

    assert importer.main([str(csv_path), "--table", "ApplicationsTable"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "created=3 updated=0 unchanged=0 aliases=0"
    assert "Kuzey" not in out


def test_main_writes_nothing_on_a_bad_file(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "applications.csv"
    csv_path.write_text("company,role,applied_date,status\nKuzey,Dev,yesterday,applied\n", encoding="utf-8")
    table = FakeTable("company_key", "role_key")
    monkeypatch.setattr(importer, "_dynamodb_table", lambda name: table)

    assert importer.main([str(csv_path), "--table", "ApplicationsTable"]) == 1
    assert table.puts == 0
    assert "line 2: applied_date" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["data/applications.csv", "applications.csv", "exports/tracker.csv"])
def test_gitignore_excludes_the_csv(path):
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
    assert result.returncode == 0, f"{path} is not ignored"


# Alias list (TRK-1, OQ-41 d)

ALIASES = """variant,canonical
KYZ,Kuzey Yazılım
Kuzey Software Inc.,Kuzey Yazılım
"""


def test_parse_aliases():
    assert parse_aliases(ALIASES) == {"KYZ": "Kuzey Yazılım", "Kuzey Software Inc.": "Kuzey Yazılım"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("variant\nKYZ\n", "missing columns: canonical"),
        ("variant,canonical\nKYZ,\n", "line 2: variant and canonical must both name a company"),
        ("variant,canonical\nKYZ,Kuzey\nkyz,Other\n", "line 3: same variant as line 2"),
        ("variant,canonical\nKYZ,Kuzey\nKuzey,Kuzey Holding\n", "line 3: this variant is also a canonical name"),
    ],
)
def test_bad_alias_lists_are_rejected(text, message):
    with pytest.raises(CsvError, match=message):
        parse_aliases(text)


def test_aliases_merge_variants_on_import():
    table = tracker()
    csv_text = "company,role,applied_date,status\nKYZ,Backend Engineer,2026-09-20,applied\n"
    import_rows(parse_csv(csv_text, parse_aliases(ALIASES)), table, parse_aliases(ALIASES))
    [row] = table.rows()
    assert row["company_key"] == "kuzey yazilim"
    assert row["company"] == "KYZ"  # as written; only the key is merged


def test_alias_item_is_stored_and_kept_out_of_rows():
    table = tracker()
    table.put_aliases({"KYZ": "Kuzey Yazılım"})
    import_rows(parse_csv(THREE_ROWS), table)
    assert table.aliases() == {"KYZ": "Kuzey Yazılım"}
    assert len(table.rows()) == 3
    assert all(not row["company_key"].startswith("#") for row in table.rows())


def test_main_with_aliases_stores_them_and_uses_them(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "applications.csv"
    csv_path.write_text("company,role,applied_date,status\nKYZ,Backend Engineer,2026-09-20,applied\n", encoding="utf-8")
    alias_path = tmp_path / "aliases.csv"
    alias_path.write_text(ALIASES, encoding="utf-8")
    table = FakeTable("company_key", "role_key")
    monkeypatch.setattr(importer, "_dynamodb_table", lambda name: table)

    assert importer.main([str(csv_path), "--table", "T", "--aliases", str(alias_path)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "created=1 updated=0 unchanged=0 aliases=2"
    assert "KYZ" not in out and "Kuzey" not in out
    assert ("kuzey yazilim", "backend engineer") in table.items


def test_main_without_aliases_uses_the_stored_list(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "applications.csv"
    csv_path.write_text("company,role,applied_date,status\nKYZ,Backend Engineer,2026-09-20,applied\n", encoding="utf-8")
    table = FakeTable("company_key", "role_key")
    TrackerTable(table).put_aliases({"KYZ": "Kuzey Yazılım"})
    monkeypatch.setattr(importer, "_dynamodb_table", lambda name: table)

    assert importer.main([str(csv_path), "--table", "T"]) == 0
    assert ("kuzey yazilim", "backend engineer") in table.items
    assert TrackerTable(table).aliases() == {"KYZ": "Kuzey Yazılım"}


def test_main_with_a_bad_alias_file_writes_nothing(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "applications.csv"
    csv_path.write_text(THREE_ROWS, encoding="utf-8")
    alias_path = tmp_path / "aliases.csv"
    alias_path.write_text("variant,canonical\nKYZ,\n", encoding="utf-8")
    table = FakeTable("company_key", "role_key")
    monkeypatch.setattr(importer, "_dynamodb_table", lambda name: table)

    assert importer.main([str(csv_path), "--table", "T", "--aliases", str(alias_path)]) == 1
    assert table.puts == 0
