"""TOOL-1 and the fixed inputs (OQ-42 d): every call is checked against its
schema before it runs, and unknown keys are refused, never dropped."""

import pytest

from inbox_agent.tools import TOOL_SPECS, WEEK3_TOOLS, ToolInputError, validate

UPSERT = TOOL_SPECS["upsert_application"]
LIST = TOOL_SPECS["list_applications"]
NOTIFY = TOOL_SPECS["notify_owner"]


def test_week_3_serves_exactly_four_tools():
    assert WEEK3_TOOLS == ("read_email", "upsert_application", "list_applications", "notify_owner")
    assert not any("send" in name or "forward" in name for name in TOOL_SPECS)


@pytest.mark.parametrize("name", WEEK3_TOOLS)
def test_every_schema_is_closed(name):
    schema = TOOL_SPECS[name].input_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema.get("required", [])) <= set(schema["properties"])


def test_the_inputs_are_the_prd_lists():
    assert set(TOOL_SPECS["read_email"].input_schema["properties"]) == {"message_id"}
    assert set(UPSERT.input_schema["properties"]) == {
        "company", "role", "status", "last_contact", "next_step", "message_id", "key_date", "key_date_type",
    }
    assert set(LIST.input_schema["properties"]) == {"status", "company", "min_days_since_contact"}
    assert set(NOTIFY.input_schema["properties"]) == {"kind", "email_class", "company", "role", "date", "count"}


def test_notify_owner_has_no_recipient_and_no_free_text():
    properties = NOTIFY.input_schema["properties"]
    assert not {"to", "recipient", "phone", "email", "address", "subject", "body", "message", "text"} & set(properties)
    # The only strings without a fixed list or date format are names, short.
    free = [k for k, rule in properties.items() if rule["type"] == "string" and "enum" not in rule and "format" not in rule]
    assert sorted(free) == ["company", "role"]
    assert all(properties[k]["maxLength"] <= 100 for k in free)
    assert properties["count"] == {"type": "integer", "minimum": 1, "maximum": 5}


@pytest.mark.parametrize("key", ["to", "recipient", "phone", "body"])
def test_unknown_keys_are_refused_without_echoing_them(key):
    with pytest.raises(ToolInputError) as error:
        validate(NOTIFY, {"kind": "urgent", "email_class": "offer", "company": "Kuzey", key: "x@example.com"})
    assert key not in str(error.value)
    assert "example.com" not in str(error.value)


def test_required_inputs():
    with pytest.raises(ToolInputError, match="company is required"):
        validate(UPSERT, {"role": "Backend Engineer"})


@pytest.mark.parametrize(
    "arguments",
    [
        {"company": 5},
        {"company": "   "},
        {"company": "K" * 101},
        {"company": "Kuzey", "status": "hired"},
        {"company": "Kuzey", "last_contact": "19 Oct 2026"},
        {"company": "Kuzey", "key_date": "next Tuesday"},
        {"company": "Kuzey", "message_id": "../../drafts"},
        {"company": "Kuzey", "next_step": "x" * 201},
    ],
)
def test_bad_upsert_inputs(arguments):
    with pytest.raises(ToolInputError):
        validate(UPSERT, arguments)


@pytest.mark.parametrize("days", [True, -1, 3651, "10", 10.0])
def test_days_must_be_a_whole_number_in_range(days):
    with pytest.raises(ToolInputError):
        validate(LIST, {"min_days_since_contact": days})


def test_good_inputs_pass_and_empty_optionals_drop():
    assert validate(LIST, {}) == {}
    assert validate(LIST, {"status": "open", "company": None, "min_days_since_contact": 10}) == {
        "status": "open",
        "min_days_since_contact": 10,
    }
    assert validate(UPSERT, {"company": "Kuzey", "key_date": "2026-11-03T14:00:00+03:00"})["key_date"]


def test_arguments_must_be_an_object():
    with pytest.raises(ToolInputError):
        validate(LIST, ["status", "open"])
