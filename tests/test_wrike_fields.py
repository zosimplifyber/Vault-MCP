import json

from wrike_fields import resolve_field_values, find_field_def, field_options

FIELD_DEFS = [
    {"id": "OWN", "title": "Owner", "type": "Contacts", "settings": {}},
    {"id": "CON", "title": "Contractor", "type": "Multiple",
     "settings": {"values": ["Xometry (Job Shop)", "S-3D (3D Printing)",
                             "Federico (Engineering/Design)"]}},
    {"id": "UNC", "title": "Uncertainty Tier", "type": "DropDown",
     "settings": {"values": ["Very High", "High", "Medium-High", "Medium", "Low"]}},
    {"id": "NOTE", "title": "Notes", "type": "Text", "settings": {}},
    {"id": "DONE", "title": "Done", "type": "Checkbox", "settings": {}},
]
CONTACTS = [
    {"id": "KZAK", "firstName": "Zak", "lastName": "O", "primaryEmail": "zak@simplifyber.com"},
    {"id": "KFED", "firstName": "Federico", "lastName": "R", "primaryEmail": "fed@x.com"},
]


def resolve(req, me_id="KME"):
    return resolve_field_values(FIELD_DEFS, CONTACTS, me_id, req)


def test_dropdown_exact_case_insensitive():
    cf, errors, applied = resolve({"Uncertainty Tier": "high"})
    assert not errors
    assert cf == [{"id": "UNC", "value": "High"}]


def test_dropdown_exact_wins_over_substring():
    # "Medium" must resolve to "Medium", not "Medium-High"
    cf, errors, _ = resolve({"Uncertainty Tier": "Medium"})
    assert not errors
    assert cf[0]["value"] == "Medium"


def test_dropdown_invalid_lists_options():
    cf, errors, _ = resolve({"Uncertainty Tier": "Extreme"})
    assert cf == []
    assert errors and "Very High" in errors[0] and "Low" in errors[0]


def test_multiple_partial_match_to_json_array():
    cf, errors, _ = resolve({"Contractor": "Xometry"})
    assert not errors
    assert cf[0]["id"] == "CON"
    assert json.loads(cf[0]["value"]) == ["Xometry (Job Shop)"]


def test_multiple_comma_separated_list():
    cf, errors, _ = resolve({"Contractor": "Xometry, S-3D"})
    assert not errors
    assert json.loads(cf[0]["value"]) == ["Xometry (Job Shop)", "S-3D (3D Printing)"]


def test_contacts_by_name():
    cf, errors, _ = resolve({"Owner": "Federico"})
    assert not errors
    assert cf == [{"id": "OWN", "value": "KFED"}]


def test_contacts_me():
    cf, errors, _ = resolve({"Owner": "me"}, me_id="KME")
    assert not errors
    assert cf[0]["value"] == "KME"


def test_contacts_by_email():
    cf, errors, _ = resolve({"Owner": "zak@simplifyber.com"})
    assert not errors
    assert cf[0]["value"] == "KZAK"


def test_unknown_field_errors():
    cf, errors, _ = resolve({"Widgets": "x"})
    assert cf == []
    assert errors and "No custom field named 'Widgets'" in errors[0]


def test_checkbox_normalizes():
    cf, errors, _ = resolve({"Done": "yes"})
    assert not errors
    assert cf[0]["value"] == "true"
    cf2, _, _ = resolve({"Done": "no"})
    assert cf2[0]["value"] == "false"


def test_text_passthrough():
    cf, errors, _ = resolve({"Notes": "hello world"})
    assert not errors
    assert cf[0]["value"] == "hello world"


def test_find_field_def_partial_unique():
    f = find_field_def(FIELD_DEFS, "uncertainty")
    assert f and f["id"] == "UNC"


def test_field_options_reads_values():
    unc = next(f for f in FIELD_DEFS if f["id"] == "UNC")
    assert field_options(unc) == ["Very High", "High", "Medium-High", "Medium", "Low"]
