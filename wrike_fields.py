"""
Pure helpers for resolving human-friendly Wrike field names/values into the
``{"id": ..., "value": ...}`` form the Wrike API expects.

No HTTP here — these functions take already-fetched custom-field definitions
and contact records, so they are fully unit-testable in isolation. The live
fetching + writing is orchestrated by WrikeRestAPI.set_task_fields_by_name.

Value encoding per field type (matches what Wrike stores/returns):
- DropDown  -> the canonical option string ("Medium-High")
- Multiple  -> a JSON-array string of canonical options ('["Xometry (Job Shop)"]')
- Contacts  -> comma-separated contact ids ("KUAA,KUAB")
- Checkbox  -> "true" / "false"
- everything else (Text/Numeric/Currency/Date/Duration/Percentage) -> str(value)
"""

import json
from typing import Any, Dict, List, Optional, Tuple


def field_options(field_def: Dict[str, Any]) -> List[str]:
    """The allowed option strings for a DropDown / Multiple custom field."""
    settings = field_def.get("settings") or {}
    values = settings.get("values")
    if isinstance(values, list) and values:
        return [str(v) for v in values]
    options = settings.get("options") or []
    return [str(o.get("value")) for o in options
            if isinstance(o, dict) and o.get("value") is not None]


def find_field_def(field_defs: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """Find a custom-field definition by title. Exact (case-insensitive) first,
    then a unique substring match; ambiguous/!found -> None."""
    n = name.strip().lower()
    exact = [f for f in field_defs if (f.get("title") or "").strip().lower() == n]
    if exact:
        return exact[0]
    subs = [f for f in field_defs if n in (f.get("title") or "").strip().lower()]
    return subs[0] if len(subs) == 1 else None


def _match_option(value: str, options: List[str]) -> Tuple[Optional[str], List[str]]:
    """(canonical, candidates). Exact case-insensitive wins; else unique
    substring. candidates holds the ambiguous set when more than one matches."""
    v = value.strip().lower()
    exact = [o for o in options if o.strip().lower() == v]
    if exact:
        return exact[0], []
    subs = [o for o in options if v in o.lower()]
    if len(subs) == 1:
        return subs[0], []
    return None, subs


def _contact_full_name(c: Dict[str, Any]) -> str:
    return f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()


def _contact_emails(c: Dict[str, Any]) -> List[str]:
    emails = [str(c.get("primaryEmail") or "").lower()]
    for p in (c.get("profiles") or []):
        if isinstance(p, dict) and p.get("email"):
            emails.append(str(p["email"]).lower())
    return [e for e in emails if e]


def _match_contact(value: str, contacts: List[Dict[str, Any]],
                   me_id: str) -> Tuple[Optional[str], List[str]]:
    """(contact_id, candidate_names). 'me'/'myself' -> me_id."""
    v = value.strip().lower()
    if v in ("me", "myself", "@me") and me_id:
        return me_id, []
    exact = [c for c in contacts
             if _contact_full_name(c).lower() == v or v in _contact_emails(c)]
    if len(exact) == 1:
        return exact[0]["id"], []
    if len(exact) > 1:
        return None, [_contact_full_name(c) for c in exact]
    subs = [c for c in contacts if v and v in _contact_full_name(c).lower()]
    if len(subs) == 1:
        return subs[0]["id"], []
    return None, [_contact_full_name(c) for c in subs]


def _option_error(name: str, raw: str, options: List[str], cands: List[str]) -> str:
    if cands:
        return (f"Field '{name}': '{raw}' is ambiguous — matches "
                f"{', '.join(cands)}. Be more specific.")
    return (f"Field '{name}': '{raw}' is not a valid option. "
            f"Valid options: {', '.join(options)}")


def _suggest_fields(name: str, field_defs: List[Dict[str, Any]]) -> str:
    titles = [f.get("title", "") for f in field_defs]
    words = [w for w in name.lower().split() if len(w) > 2]
    close = [t for t in titles if any(w in t.lower() for w in words)]
    pick = close[:8] if close else titles[:8]
    tail = "" if close else " (call wrike_list_custom_fields to see all)"
    return ", ".join(pick) + tail


def resolve_field_values(
    field_defs: List[Dict[str, Any]],
    contacts: List[Dict[str, Any]],
    me_id: str,
    requested: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], List[str], List[Dict[str, Any]]]:
    """Resolve a {field_name: value} mapping to Wrike custom-field tuples.

    Returns (custom_fields, errors, applied):
      custom_fields -> [{"id", "value"}] ready for the API
      errors        -> human-readable problems (any error => write nothing)
      applied       -> [{"name","id","type","value"}] for a result summary
    """
    custom_fields: List[Dict[str, str]] = []
    errors: List[str] = []
    applied: List[Dict[str, Any]] = []

    for name, raw in requested.items():
        fdef = find_field_def(field_defs, name)
        if not fdef:
            errors.append(f"No custom field named '{name}'. "
                          f"Did you mean: {_suggest_fields(name, field_defs)}")
            continue
        ftype = fdef.get("type")
        fid = fdef["id"]

        if ftype == "DropDown":
            canon, cands = _match_option(str(raw), field_options(fdef))
            if canon is None:
                errors.append(_option_error(name, raw, field_options(fdef), cands))
                continue
            value = canon
        elif ftype == "Multiple":
            items = raw if isinstance(raw, list) else \
                [s.strip() for s in str(raw).split(",") if s.strip()]
            chosen, bad = [], False
            for it in items:
                canon, cands = _match_option(str(it), field_options(fdef))
                if canon is None:
                    errors.append(_option_error(name, it, field_options(fdef), cands))
                    bad = True
                    break
                chosen.append(canon)
            if bad:
                continue
            value = json.dumps(chosen)
        elif ftype == "Contacts":
            items = raw if isinstance(raw, list) else \
                [s.strip() for s in str(raw).split(",") if s.strip()]
            ids, bad = [], False
            for it in items:
                cid, cands = _match_contact(str(it), contacts, me_id)
                if cid is None:
                    hint = f" Closest: {', '.join(cands)}." if cands else ""
                    errors.append(f"Field '{name}': could not resolve contact "
                                  f"'{it}'.{hint}")
                    bad = True
                    break
                ids.append(cid)
            if bad:
                continue
            value = ",".join(ids)
        elif ftype == "Checkbox":
            value = "true" if str(raw).strip().lower() in \
                ("true", "yes", "1", "on", "checked") else "false"
        else:
            value = str(raw)

        custom_fields.append({"id": fid, "value": value})
        applied.append({"name": fdef.get("title"), "id": fid,
                        "type": ftype, "value": value})

    return custom_fields, errors, applied
