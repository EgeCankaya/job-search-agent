"""TRK-1 normalization and row matching, shared by the TRK-3 import and,
from week 3, `upsert_application`.

A company is lowercased, Turkish letters are folded (ş→s, ı→i, ğ→g, ü→u,
ö→o, ç→c), punctuation becomes spaces, trailing legal suffixes are removed,
and the alias list is applied. A role gets the same treatment without the
suffixes and aliases.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping

from inbox_agent import settings
from inbox_agent.tracker import CLOSED_STATUSES

# DynamoDB key attributes can't be empty, so a missing role gets this key.
# Normalized text never contains "-", so it can't collide with a real role.
NO_ROLE_KEY = "-"

# Folded before lowercasing: Python lowercases "İ" to "i" plus a combining dot.
_TURKISH_FOLD = str.maketrans(
    {"ş": "s", "Ş": "s", "ı": "i", "I": "i", "İ": "i", "ğ": "g", "Ğ": "g",
     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"}
)

# Trailing token sequences, as they look after normalization ("A.Ş." -> "a s").
# Removed repeatedly from the end, so "San. ve Tic. A.Ş." goes in two steps.
LEGAL_SUFFIXES = (
    ("a", "s"),
    ("as",),
    ("ltd", "sti"),
    ("sti",),
    ("ltd",),
    ("inc",),
    ("gmbh",),
    ("llc",),
    ("plc",),
    ("corp",),
    ("san", "ve", "tic"),
    ("sanayi", "ve", "ticaret"),
    ("tic",),
    ("ticaret",),
)


def normalize_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.translate(_TURKISH_FOLD).lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", plain).split())


def _strip_legal_suffixes(tokens: list[str]) -> list[str]:
    stripped = True
    while stripped:
        stripped = False
        for suffix in LEGAL_SUFFIXES:
            n = len(suffix)
            # Never strip a name down to nothing ("Inc" stays "inc").
            if len(tokens) > n and tuple(tokens[-n:]) == suffix:
                tokens = tokens[:-n]
                stripped = True
                break
    return tokens


def company_key(company: str, aliases: Mapping[str, str] | None = None) -> str:
    aliases = settings.COMPANY_ALIASES if aliases is None else aliases
    key = " ".join(_strip_legal_suffixes(normalize_text(company).split()))
    if not key:
        raise ValueError("company is empty after normalization")
    normalized_aliases = {company_key(k, {}): company_key(v, {}) for k, v in aliases.items()}
    return normalized_aliases.get(key, key)


def role_key(role: str | None) -> str:
    return normalize_text(role or "") or NO_ROLE_KEY


def is_open(row: Mapping) -> bool:
    return row.get("status") not in CLOSED_STATUSES


def match_application(
    rows: Iterable[Mapping],
    company: str,
    role: str | None,
    aliases: Mapping[str, str] | None = None,
) -> Mapping | None:
    """The row an email for `company` and `role` belongs to, or None for a new row.

    With a role, the row with the same company and role keys. Without one, the
    company's only open row, or, if there are several, the open row with the
    latest last contact date (TRK-1).
    """
    ckey = company_key(company, aliases)
    candidates = [row for row in rows if row["company_key"] == ckey]
    rkey = role_key(role)
    if rkey != NO_ROLE_KEY:
        return next((row for row in candidates if row["role_key"] == rkey), None)
    open_rows = [row for row in candidates if is_open(row)]
    if not open_rows:
        return None
    # ISO dates sort as strings; a row with no last contact sorts first.
    return max(open_rows, key=lambda row: row.get("last_contact") or "")
