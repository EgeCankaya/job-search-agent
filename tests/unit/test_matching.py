"""TRK-1 normalization and matching (§12: company normalization and matching).

Fictional companies only.
"""

import pytest

from inbox_agent.matching import NO_ROLE_KEY, company_key, match_application, normalize_text, role_key


@pytest.mark.parametrize(
    "name",
    [
        "Kuzey Yazılım",
        "KUZEY YAZILIM A.Ş.",
        "Kuzey Yazılım AŞ",
        "kuzey yazilim a.s.",
        "Kuzey Yazılım Ltd. Şti.",
        "Kuzey Yazılım San. ve Tic. A.Ş.",
        "  Kuzey   Yazılım, A.Ş.  ",
    ],
)
def test_turkish_variants_share_one_key(name):
    # The PRD's TRK-1 suffix case, with a fictional company.
    assert company_key(name, aliases={}) == "kuzey yazilim"


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Example Robotics Inc.", "example robotics"),
        ("Example Robotics GmbH", "example robotics"),
        ("Example Robotics LLC", "example robotics"),
        ("Çağlayan Döküm", "caglayan dokum"),
        ("İLKE ÖĞRETİM", "ilke ogretim"),
        ("Işık Gıda", "isik gida"),
        ("Café Exemplo", "cafe exemplo"),
    ],
)
def test_legal_suffixes_and_letters(name, key):
    assert company_key(name, aliases={}) == key


def test_turkish_dotted_capital_i_folds_cleanly():
    # Python lowercases "İ" to "i" plus a combining dot; the fold must not.
    assert normalize_text("İSTANBUL") == "istanbul"


def test_name_that_is_only_a_suffix_is_kept():
    assert company_key("Inc", aliases={}) == "inc"


def test_empty_company_is_rejected():
    with pytest.raises(ValueError):
        company_key(" .,- ", aliases={})


def test_alias_list_maps_variants_to_one_key():
    aliases = {"KYZ": "Kuzey Yazılım"}
    assert company_key("KYZ A.Ş.", aliases) == company_key("Kuzey Yazılım", aliases) == "kuzey yazilim"


def test_alias_list_defaults_to_settings(monkeypatch):
    from inbox_agent import settings

    monkeypatch.setattr(settings, "COMPANY_ALIASES", {"kyz": "kuzey yazilim"})
    assert company_key("KYZ") == "kuzey yazilim"


def test_role_key():
    assert role_key("Senior Backend Engineer (Python)") == "senior backend engineer python"
    assert role_key("Yazılım Mühendisi") == "yazilim muhendisi"
    assert role_key("") == role_key(None) == NO_ROLE_KEY


def row(company, role, status="applied", last_contact="2026-10-01"):
    return {
        "company_key": company_key(company, {}),
        "role_key": role_key(role),
        "status": status,
        "last_contact": last_contact,
    }


def test_match_with_role_needs_same_company_and_role():
    rows = [row("Kuzey Yazılım", "Backend Engineer"), row("Kuzey Yazılım", "Data Engineer")]
    assert match_application(rows, "KUZEY YAZILIM A.Ş.", "backend engineer", {}) is rows[0]
    assert match_application(rows, "Kuzey Yazılım", "ML Engineer", {}) is None
    assert match_application(rows, "Güney Veri", "Backend Engineer", {}) is None


def test_match_without_role_takes_the_only_open_row():
    rows = [row("Kuzey Yazılım", "Backend Engineer"), row("Kuzey Yazılım", "Data Engineer", status="rejected")]
    assert match_application(rows, "Kuzey Yazılım", None, {}) is rows[0]


def test_match_without_role_takes_latest_contact_among_open_rows():
    rows = [
        row("Kuzey Yazılım", "Backend Engineer", last_contact="2026-09-01"),
        row("Kuzey Yazılım", "Data Engineer", status="interviewing", last_contact="2026-10-10"),
        row("Kuzey Yazılım", "ML Engineer", status="withdrawn", last_contact="2026-10-15"),
    ]
    assert match_application(rows, "Kuzey Yazılım", "", {}) is rows[1]


def test_match_without_role_and_no_open_row_is_a_new_row():
    rows = [row("Kuzey Yazılım", "Backend Engineer", status="rejected")]
    assert match_application(rows, "Kuzey Yazılım", None, {}) is None
