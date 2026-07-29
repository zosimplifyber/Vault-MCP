"""
Headless engines for the six Release Workflow steps.

Each ``run_*`` function returns a :class:`StepOutcome`. Steps that write to
Vault or SharePoint do the read-only half immediately and hand back a
``pending_apply`` callable holding the write, so the GUI can show a preview and
require a second, explicit click before anything changes.

No Tkinter import belongs in this module — that is what makes it testable.
The GUI wrapper lives in ``gui/release_workflow.py``.

See docs/superpowers/specs/2026-07-29-release-workflow-file-driven-design.md
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Output-panel tags, matching the tags configured on the wizard's Text widget.
TAG_INFO = "info"
TAG_PASS = "pass"
TAG_FAIL = "fail"
TAG_WARN = "warn"
TAG_DIM = "dim"
TAG_H2 = "h2"

# Every tag an engine may hand back in a StepOutcome's ``lines``. Tk's
# Text.insert does not raise on an unconfigured tag — it just renders
# unstyled — so this tuple is the only thing that catches a renamed or
# dropped ``tag_configure`` on the wizard side before it ships as a subtly
# wrong-looking window.
#
# Deliberately excludes wizard chrome such as "h1" / "step_banner": those
# belong to the wizard's own headers, not to engine output. Do not add them
# here — a later task hardcoding one of those into an engine is the bug this
# tuple exists to prevent from spreading.
ALL_TAGS = (TAG_INFO, TAG_PASS, TAG_FAIL, TAG_WARN, TAG_DIM, TAG_H2)


@dataclass
class StepOutcome:
    """What a step reports back to the wizard.

    ``pending_apply`` is the whole preview-then-write mechanism: when it is
    set, the step has computed a preview and staged a write that has NOT
    happened. The wizard renders ``lines``, moves the step to REVIEW, and waits
    for a click. Calling it performs the write and returns the final outcome.

    ``result`` is the channel step 1's compliance dict travels through to
    steps 2 and 3 (see :func:`property_check_blocked`, :func:`file_version_ids`,
    :func:`file_master_ids`). It is ``None`` for every other step.
    """
    ok: bool
    summary: str
    lines: list[tuple[str, str]] = field(default_factory=list)
    pending_apply: Callable[[], StepOutcome] | None = None
    result: Any = None

    @property
    def needs_review(self) -> bool:
        """True when a write is staged but not performed.

        Takes precedence over ``ok``: a preview may report problems (ok=False)
        and still offer Apply — step 5 previewing drawing gaps is exactly
        that. Callers must test this before ``ok``, and "Run all remaining"
        must halt on it.
        """
        return self.pending_apply is not None


def _child_failed(child: dict[str, Any]) -> bool:
    """A child counts as failed if it errored while resolving, or failed its
    rule check — but NOT for simply having no rule set (that's SKIP)."""
    if child.get("error"):
        return True
    return bool((child.get("report") or {}).get("failed", 0))


def property_check_blocked(
    compliance: Optional[dict[str, Any]], *, force: bool
) -> Optional[str]:
    """Return a reason string when Sync / Release must not run, else None.

    Only steps 2 and 3 consult this. The BOM steps are deliberately not gated:
    a purchasing sheet is useful while properties are still being fixed.

    Checked in this order:

    1. No step 1 result at all (falsy ``compliance``) — blocks; ``force``
       never overrides. Steps 2 and 3 take their file list from step 1, so
       with no step 1 there is no work to force past.
    2. ``children_error`` is set — blocks; ``force`` never overrides. The
       child list is silently incomplete, so the user cannot consent to a
       partial release when they cannot know what is missing. Re-running
       step 1 is cheap.
    3. No rule set for the top file's category (``category_resolved``
       falsy, ``report`` is ``None``) — blocks; ``force`` DOES override.
       This is deliberately forceable: a category may legitimately have no
       rules, and an un-forceable block would make the wizard unusable for
       that work. The gate exists to stop *unknowing* release of unchecked
       files, so the message says nothing was checked — it must not imply
       failure.
    4. Failing properties, at the top file or any child — blocks; ``force``
       DOES override.

    A child with no rule set of its own never blocks on its own account —
    that matches ``child_status`` (check_file_properties.py), which treats an
    unresolved child category as SKIP rather than FAIL. Only the top file's
    unresolved category escalates the whole release, matching
    ``result_exit_code``.
    """
    if not compliance:
        return "Run step 1 (Property Check) first — it supplies the file list."
    if compliance.get("children_error"):
        return (
            "Property Check could not resolve every CAD BOM child "
            f"({compliance['children_error']}). The child list is "
            "incomplete, so this cannot be forced past — re-run step 1."
        )
    if force:
        return None
    if not compliance.get("category_resolved"):
        return (
            "Property Check found no rule set for this file's category, so "
            "nothing was checked. Tick 'Force past compliance gate' to "
            "continue anyway."
        )
    failed = bool((compliance.get("report") or {}).get("failed", 0))
    kids = any(_child_failed(c) for c in (compliance.get("children") or []))
    if failed or kids:
        return (
            "Property Check found failures. Fix them and re-run step 1, or "
            "tick 'Force past compliance gate' to continue anyway."
        )
    return None


def _entries(compliance: dict[str, Any]) -> list[dict[str, Any]]:
    """Top file first, then every CAD BOM child — errored ones included;
    callers filter.

    Returns ``[]`` for a falsy ``compliance`` (``None`` included) — the one
    guard ``file_version_ids``, ``file_master_ids`` and ``unresolved_files``
    all rely on, so ``compliance=None`` behaves the same everywhere instead
    of some of them returning ``[]`` and others raising ``AttributeError``.

    The top file's own dict (``compliance["info"]``) carries no
    ``file_name`` of its own — ``fetch_file`` (check_file_properties.py)
    never copies it down; the name lives one level up, on ``compliance``
    itself. Without borrowing it here, a preview that names every dropped
    file gets every CAD BOM child right and reports the one file the user
    actually typed in as ``"(unnamed)"``.
    """
    if not compliance:
        return []
    info = dict(compliance.get("info") or {})
    info.setdefault("file_name", compliance.get("file_name") or "")
    out = [info]
    out.extend(compliance.get("children") or [])
    return out


def file_version_ids(compliance: dict[str, Any]) -> list[str]:
    """File-version IDs for every file in the assembly, top first.

    Order is deterministic and de-duplicated: a child used more than once, or
    one that repeats the top-level file, is synced once.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for entry in _entries(compliance):
        fid = str(entry.get("file_version_id") or "").strip()
        if fid and fid not in seen:
            seen.add(fid)
            ids.append(fid)
    return ids


def file_master_ids(compliance: dict[str, Any]) -> list[int]:
    """File *master* IDs — what the SDK lifecycle call takes, not version IDs.

    Anything blank or non-numeric is dropped rather than guessed at; a bad ID
    would fail the whole lifecycle batch.
    """
    ids: list[int] = []
    seen: set[int] = set()
    for entry in _entries(compliance):
        raw = entry.get("file_id")
        if raw in (None, ""):
            continue
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            logger.warning("Skipping unparseable file master id %r", raw)
            continue
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids


def unresolved_files(compliance: dict[str, Any]) -> list[tuple[str, str]]:
    """Files carrying an unusable Vault ID, and which one is unusable.

    ``file_version_ids`` and ``file_master_ids`` both drop bad entries
    silently, by design (a bad ID would fail the whole batch). Steps 2 and 3
    must surface what got dropped in their own preview — but only the kind of
    drop that actually affects them.

    Returns ``(name, missing)`` where ``missing`` is ``"version"``,
    ``"master"`` or ``"both"``. A file with a version ID but no master ID
    syncs fine and is only step 3's problem; reporting it as skipped in
    step 2's preview would make that preview lie, which trains people to
    click through it — the exact failure mode this visibility fix exists to
    prevent. Steps 2 and 3 each filter to the kind they actually drop.

    Returns ``[]`` for a falsy ``compliance`` (via ``_entries``'s own
    guard) — unreachable in practice, since the gate
    (:func:`property_check_blocked`) blocks a missing step 1 result
    un-forceably. This is therefore not a standalone health check.
    """
    out: list[tuple[str, str]] = []
    for entry in _entries(compliance):
        vid = str(entry.get("file_version_id") or "").strip()
        raw_mid = entry.get("file_id")
        mid_ok = raw_mid not in (None, "")
        if mid_ok:
            try:
                int(raw_mid)
            except (TypeError, ValueError):
                mid_ok = False
        if vid and mid_ok:
            continue
        if not vid and not mid_ok:
            missing = "both"
        elif not vid:
            missing = "version"
        else:
            missing = "master"
        out.append((str(entry.get("file_name") or "(unnamed)"), missing))
    return out


def _check_file_name(**kwargs: Any) -> dict[str, Any]:
    """Thin seam over check_file_properties.check_file_name.

    Imported lazily and wrapped so tests can substitute it without a live
    Vault, and so a missing scripts/ path fails at the step rather than at
    module import.
    """
    import asyncio

    from check_file_properties import check_file_name
    return asyncio.run(check_file_name(**kwargs))


def run_property_check(
    file_name: str, *, api: Any = None, vault_id: str = "",
) -> StepOutcome:
    """Step 1 — run the file property rules over the assembly and its CAD BOM.

    Read-only: there is no ``pending_apply``. The result dict is carried on
    ``StepOutcome.result`` because steps 2 and 3 take their file list from it.
    """
    try:
        result = _check_file_name(
            file_name=file_name, recursive=True, api=api, vault_id=vault_id,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        return StepOutcome(ok=False, summary=str(exc),
                           lines=[(f"  [fail] {exc}", TAG_FAIL)])

    lines: list[tuple[str, str]] = []
    raw_report = result.get("report")
    # ``report`` is None — never {} — when no rule set matches the top
    # file's category (check_file_properties.evaluate_against_rules;
    # result_exit_code returns 2 for this case). That means nothing was
    # checked, and must never render as a green PASS: the same "absent data
    # reads as success" bug already fixed once in property_check_blocked.
    unchecked = raw_report is None
    report = raw_report or {}
    total = report.get("total", 0)
    passed = report.get("passed", 0)
    failed = report.get("failed", 0)
    category = (result.get("category_resolved")
                or result.get("category_raw") or "(no rule set)")
    props = (result.get("info") or {}).get("properties") or {}

    lines.append((f"  Top file — {file_name}  [{category}]", TAG_H2))
    lines.append((f"    revision={props.get('Revision', '?')!r}  "
                  f"state={props.get('State', '?')!r}", TAG_DIM))
    if unchecked:
        lines.append((f"    SKIP  no rule set matches category {category!r} "
                      f"— nothing was checked", TAG_WARN))
    else:
        lines.append((f"    {'PASS' if not failed else 'FAIL'}  "
                      f"({passed}/{total} checks pass)",
                      TAG_PASS if not failed else TAG_FAIL))

    for r in report.get("results") or []:
        if r.get("passed"):
            continue
        value = r.get("value")
        shown = "(empty)" if value in (None, "") else str(value)[:40]
        lines.append((f"      • {r['property']:24s} = {shown}", TAG_FAIL))
        for reason in r.get("failures") or []:
            lines.append((f"          → {reason}", TAG_FAIL))

    children = result.get("children") or []
    bad_children = [c for c in children if _child_failed(c)]
    if result.get("children_error"):
        lines.append((f"  [warn] CAD BOM: {result['children_error']}", TAG_WARN))
    if children:
        lines.append(("", TAG_DIM))
        lines.append((f"  CAD BOM — {len(children)} file(s), "
                      f"{len(bad_children)} with problems",
                      TAG_PASS if not bad_children else TAG_FAIL))
    for child in bad_children:
        name = child.get("file_name") or "?"
        if child.get("error"):
            lines.append((f"      • {name:20s} ERROR: {child['error']}", TAG_FAIL))
            continue
        bad = [r for r in ((child.get("report") or {}).get("results") or [])
               if not r.get("passed")]
        names = ", ".join(b["property"] for b in bad)
        lines.append((f"      • {name:20s} {len(bad)} fail · {names}", TAG_FAIL))

    # A failed CAD BOM walk (children_error) means the child list is
    # incomplete — an unseen child could be failing rules the walk never
    # reached. An unchecked top file means literally nothing was verified.
    # Either one reading as a pass is exactly the "absent data reads as
    # success" bug already fixed once in property_check_blocked; this closes
    # the same hole in the step engine itself.
    ok = (not failed and not bad_children
          and not result.get("children_error") and not unchecked)
    if unchecked:
        summary = (f"Property Check: no rule set for category {category!r} "
                   f"— nothing was checked on the top file "
                   f"({len(bad_children)} of {len(children)} child file(s) "
                   f"failing).")
    else:
        summary = (f"Property Check: {passed}/{total} on the top file, "
                   f"{len(bad_children)} of {len(children)} child file(s) "
                   f"failing.")
    return StepOutcome(ok=ok, summary=summary, lines=lines, result=result)


def run_sync_properties(
    api: Any, vault_id: str, compliance: dict[str, Any],
) -> StepOutcome:
    """Step 2 — queue Autodesk.Vault.SyncProperties for every file.

    Preview lists the files; the apply submits. Job params must be PascalCase:
    Vault's /jobs endpoint is case-sensitive there even though its JSON
    response echoes camelCase.
    """
    import asyncio

    entries = _entries(compliance)
    version_ids = file_version_ids(compliance)
    if not version_ids:
        if not entries:
            return StepOutcome(
                ok=True, summary="No files to sync.",
                lines=[("  [warn] No CAD files found — nothing to sync.",
                        TAG_WARN)],
            )
        # Files WERE found by step 1 — every one of them just failed to
        # carry a usable version ID. That is a total drop, not "nothing to
        # do": it must not read the same as an assembly with zero CAD files,
        # or a single API shape change that blanks every ID paints the whole
        # step green.
        lines = [(f"  [fail] {len(entries)} file(s) found, but none carry a "
                  f"usable file-version ID — nothing can be synced.",
                  TAG_FAIL)]
        for name, missing in unresolved_files(compliance):
            if missing in ("version", "both"):
                lines.append((f"      ! {name}: no file-version ID", TAG_FAIL))
        return StepOutcome(
            ok=False,
            summary=(f"Sync Properties: {len(entries)} file(s) found, 0 "
                     f"usable — nothing was synced."),
            lines=lines,
        )

    names = _names_by_version_id(compliance)
    lines = [(f"  {len(version_ids)} file(s) will be synced:", TAG_INFO)]
    lines += [(f"      · {names.get(fid, fid)}", TAG_DIM) for fid in version_ids]

    # Files this step will drop must be named here. The preview is the human
    # checkpoint; a file silently missing from the batch is a partial sync
    # reported as success. Only report what THIS step drops — a file with a
    # version ID but no master ID syncs fine and is step 3's problem, so
    # claiming it will be skipped here would make the preview lie.
    for name, missing in unresolved_files(compliance):
        if missing in ("version", "both"):
            lines.append(
                (f"      ! {name}: no file-version ID — cannot be synced",
                 TAG_WARN))

    applied_once = False

    def apply() -> StepOutcome:
        nonlocal applied_once
        if applied_once:
            # The GUI's own state machine should prevent a second click, but
            # this engine is what's under test, and Tasks 8-10 copy this
            # shape — it must refuse to submit twice on its own account.
            return StepOutcome(
                ok=False,
                summary=("Sync Properties: this preview was already applied "
                         "— re-run step 2 for a fresh one."),
                lines=[("  [warn] Already applied once this run — nothing "
                        "submitted again.", TAG_WARN)],
            )
        applied_once = True

        async def submit_all() -> tuple[int, int, list[tuple[str, str]]]:
            ok_n = bad_n = 0
            out: list[tuple[str, str]] = []
            for fid in version_ids:
                name = names.get(fid, fid)
                try:
                    resp = await api.submit_job(
                        vault_id=vault_id,
                        job_type="Autodesk.Vault.SyncProperties",
                        params={"FileVersionId": fid},
                        description=f"SyncProperties: {name}",
                        priority=10,
                    )
                except Exception as exc:  # noqa: BLE001 — one file must not
                    # sink the ones already submitted before it.
                    out.append((f"    [fail] {name}: {exc}", TAG_FAIL))
                    bad_n += 1
                    continue
                if resp.get("error"):
                    out.append((f"    [fail] {name}: {resp.get('data')}",
                                TAG_FAIL))
                    bad_n += 1
                else:
                    data = resp.get("data") or {}
                    job_id = str((data.get("job") or {}).get("id")
                                 or data.get("id") or "?")
                    out.append((f"    [ok]   {name}  (job {job_id})", TAG_PASS))
                    ok_n += 1
            return ok_n, bad_n, out

        ok_n, bad_n, out = asyncio.run(submit_all())
        return StepOutcome(
            ok=bad_n == 0,
            summary=f"Sync Properties: {ok_n} queued, {bad_n} failed.",
            lines=out,
        )

    return StepOutcome(
        ok=True,
        summary=f"{len(version_ids)} file(s) ready to sync — click Apply to queue.",
        lines=lines, pending_apply=apply,
    )


def _sdk():
    """Import the vault_sdk bridge lazily.

    It shells out to PowerShell and the .NET SDK, so importing it at module
    load would make every release_steps import pay for a bridge that most
    steps never touch. A fixture replaces this in tests.
    """
    import vault_sdk
    return vault_sdk


def run_release_files(
    api: Any, vault_id: str, compliance: dict[str, Any], *,
    target_state: str, state_id: Optional[int] = None,
) -> StepOutcome:
    """Step 3 — promote every file to the target lifecycle state.

    The state must be resolved inside the *file* lifecycle definition, not
    the item one, so we look up the first file (by ``file_master_ids(...)[0]``)
    and search its own definition. If a child sits in a different file
    lifecycle than the top file, the id resolved from the top file may not
    be the right one for it — that fails loudly against Vault rather than
    silently guessing, and ``state_id`` exists as the override for exactly
    that case. ``state_id`` short-circuits the lookup entirely when set.

    ``api`` and ``vault_id`` are accepted but unused here — this step talks
    to Vault only through the SDK bridge (``_sdk()``), never the REST API.
    They are kept for signature uniformity with the other step engines,
    which Task 12's dispatch table calls positionally; dropping them would
    break that symmetry for no benefit.
    """
    entries = _entries(compliance)
    masters = file_master_ids(compliance)
    if not masters:
        if not entries:
            return StepOutcome(
                ok=True, summary="No files to release.",
                lines=[("  [warn] No CAD files found to release.", TAG_WARN)],
            )
        # Files WERE found by step 1 — every one of them just failed to
        # carry a usable master ID. Releasing 0 of N files found must not
        # read the same as an assembly with zero CAD files: "nothing to do"
        # and "everything was dropped" cannot share an outcome.
        lines = [(f"  [fail] {len(entries)} file(s) found, but none carry a "
                  f"usable file master ID — nothing can be released.",
                  TAG_FAIL)]
        for name, missing in unresolved_files(compliance):
            if missing in ("master", "both"):
                lines.append((f"      ! {name}: no file master ID", TAG_FAIL))
        return StepOutcome(
            ok=False,
            summary=(f"Release Files: {len(entries)} file(s) found, 0 "
                     f"usable — nothing was released."),
            lines=lines,
        )

    try:
        sdk = _sdk()
    except Exception as exc:  # noqa: BLE001 — the bridge module itself can
        # fail to import; that must convert to a failed outcome too.
        return StepOutcome(
            ok=False, summary=f"Could not load the Vault SDK bridge: {exc}",
            lines=[(f"  [fail] {exc}", TAG_FAIL)])

    resolved = state_id
    if resolved is None:
        try:
            first = sdk.lookup_file(masters[0])
            if not first.get("found"):
                msg = f"Could not look up file masterId={masters[0]}"
                return StepOutcome(ok=False, summary=msg,
                                   lines=[(f"  [fail] {msg}", TAG_FAIL)])
            # find_state_id_for_file reaches the same PowerShell bridge as
            # lookup_file — a non-zero exit, timeout, or bad JSON there must
            # be caught here too, not just around the lookup that precedes it.
            resolved = sdk.find_state_id_for_file(first, target_state)
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(ok=False, summary=f"File lookup failed: {exc}",
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])

    if resolved is None:
        msg = (f"Could not resolve a lifecycle state id for {target_state!r} "
               f"in the file's lifecycle. Set 'State ID (override)'.")
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    lines = [
        (f"  {len(masters)} file(s) will move to '{target_state}' "
         f"(state_id={resolved}).", TAG_INFO),
        ("  This changes lifecycle state in Vault and cannot be undone "
         "from here.", TAG_WARN),
    ]

    # Name every file this step will drop. Releasing 37 of 40 files and
    # reporting "37 moved" is a partial release reported as success — the
    # engineer has no baseline to compare 37 against. Report only what THIS
    # step drops: a missing master ID is what excludes a file from the
    # lifecycle batch.
    for name, missing in unresolved_files(compliance):
        if missing in ("master", "both"):
            lines.append(
                (f"      ! {name}: no file master ID — will NOT be released",
                 TAG_WARN))

    applied_once = False

    def apply() -> StepOutcome:
        nonlocal applied_once
        if applied_once:
            return StepOutcome(
                ok=False,
                summary=("Release Files: this preview was already applied "
                         "— re-run step 3 for a fresh one."),
                lines=[("  [warn] Already applied once this run — nothing "
                        "changed again.", TAG_WARN)],
            )
        applied_once = True
        try:
            result = sdk.update_file_lifecycle_states(
                masters, resolved,
                comment=f"Released via Release Workflow to {target_state}",
            )
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(ok=False, summary=f"Release failed: {exc}",
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])

        # Never assume a full success from what the bridge didn't confirm.
        # {"updated": 0} is directly reachable (vault_sdk.ps1's `$updated`
        # can be empty); an empty dict, or an explicit None, means the
        # bridge told us nothing at all — that must fail, not default to
        # "everything requested moved". The denominator is always shown:
        # "37 moved" gives no baseline, "37 of 40" does.
        requested = len(masters)
        moved = result.get("updated")
        if moved is None:
            msg = (f"Release Files: the Vault bridge did not report how "
                   f"many file(s) moved (requested {requested}) — treating "
                   f"as failed since it cannot be confirmed.")
            return StepOutcome(
                ok=False, summary=msg,
                lines=[(f"  [fail] Bridge response carried no 'updated' "
                        f"count (requested {requested}).", TAG_FAIL)],
            )

        ok = moved == requested
        return StepOutcome(
            ok=ok,
            summary=(f"Released {moved} of {requested} file(s) to "
                     f"'{target_state}'."),
            lines=[(f"  [{'ok' if ok else 'fail'}] Released {moved} of "
                    f"{requested} file(s).", TAG_PASS if ok else TAG_FAIL)],
        )

    return StepOutcome(
        ok=True,
        summary=(f"{len(masters)} file(s) ready to move to '{target_state}' "
                 f"(state_id={resolved}) — click Apply."),
        lines=lines, pending_apply=apply,
    )


def _list_sync_deps():
    """Return (connect_client, add_missing_bom_rows, bom_dataframe_from_file).

    Bundled behind one seam so a single monkeypatch replaces the whole
    SharePoint path in tests.
    """
    import bom_list_sync
    from supplier_pricing.cli import _connect_client
    return (_connect_client, bom_list_sync.add_missing_bom_rows,
            bom_list_sync.bom_dataframe_from_file)


# The non-interactive Graph connect fails this way when the token cache is
# empty; the fix is a one-off interactive probe, so say so rather than
# surfacing a bare "not signed in".
_PROBE_HINT = ("Run once in a terminal to sign in:\n"
               "    python -m supplier_pricing probe")


def run_purchased_parts_list(
    bom_path: str, *, buy_only: bool = True, update_existing: bool = False,
) -> StepOutcome:
    """Step 4 — add BOM parts missing from the Engineering Purchased Parts list.

    Preview is a dry run that names every proposed addition. That matters:
    the list is keyed on SF-###### numbers, so ISO/DIN fasteners in an
    Inventor BOM never match and would otherwise be added silently as new
    parts.
    """
    connect, add_rows, parse_bom = _list_sync_deps()

    df, err = parse_bom(bom_path)
    if err:
        return StepOutcome(ok=False, summary=f"BOM could not be read: {err}",
                           lines=[(f"  [fail] {err}", TAG_FAIL)])

    sources = {"Buy", "Other"} if buy_only else None

    def sync(dry_run: bool) -> dict[str, Any]:
        client = connect()
        return add_rows(client, df, dry_run=dry_run, sources=sources,
                        update_existing=update_existing)

    try:
        report = sync(dry_run=True)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        msg = str(exc)
        if "not signed in" in msg.lower():
            msg = f"{msg}\n{_PROBE_HINT}"
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    # A None/blank result from the dry run is absent data, not a clean
    # answer (R1) — report.get(...) below would otherwise raise
    # AttributeError and escape the step entirely.
    if not report:
        msg = "Purchased Parts List: the dry run returned no result."
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    missing = report.get("missing") or []
    checked = report.get("checked", 0)
    present = report.get("already_present", 0)
    skipped_no_name = report.get("skipped_no_name", 0)
    skipped_source = report.get("skipped_source", 0)
    rows_out = report.get("rows") or []
    would_update = [r.get("name") for r in rows_out
                    if r.get("status") == "would_update"]

    lines = [(f"  Dry run — {checked} BOM part(s) checked, {present} already "
              f"listed, {len(missing)} new.", TAG_INFO)]
    for name in missing:
        lines.append((f"      + {name}", TAG_WARN))
    for name in would_update:
        lines.append((f"      ~ {name}  (existing row will be updated)",
                      TAG_WARN))
    if skipped_no_name:
        lines.append((f"  [warn] {skipped_no_name} row(s) had no "
                      f"file name and were skipped.", TAG_WARN))
    if skipped_source:
        lines.append((f"  [warn] {skipped_source} row(s) were skipped by "
                      f"the Buy-only source filter — their Source was not "
                      f"in {{'Buy', 'Other'}}.", TAG_WARN))

    # R3: zero rows CHECKED is a different outcome from zero rows MISSING.
    # A genuinely current list gets here with checked>0 and missing==[] —
    # that stays ok=True below. This branch is for BOMs where every row was
    # filtered out before it could even be compared against the list, which
    # must never render as "up to date". buy_only=True is the wizard's
    # default, and add_missing_bom_rows filters Source in {"Buy", "Other"};
    # a BOM exported with no Source column gives every row source="", which
    # matches nothing, so every real purchased part is dropped silently —
    # this is the concrete shape that must not go green.
    if checked == 0:
        reasons = []
        if skipped_no_name:
            reasons.append(f"{skipped_no_name} row(s) had no file name")
        if skipped_source:
            reasons.append(
                f"{skipped_source} row(s) had no Source in "
                f"{{'Buy', 'Other'}} — check that the BOM export has a "
                f"Source column, and that the Buy-only filter is what you "
                f"intended")
        if not reasons:
            reasons.append("the BOM had no rows to check")
        summary = ("Purchased Parts List: 0 part(s) checked — nothing "
                   "could be verified against the list ("
                   + "; ".join(reasons) + ").")
        return StepOutcome(ok=False, summary=summary, lines=lines)

    if not missing:
        return StepOutcome(
            ok=True,
            summary=f"Purchased Parts List is up to date — 0 of {checked} missing.",
            lines=lines,
        )

    # Captured here, not re-derived inside apply() (R6): the approved count
    # is the number the preview showed, and apply's own report is judged
    # against that, never against itself.
    expected = len(missing)
    applied_once = False

    def apply() -> StepOutcome:
        nonlocal applied_once
        if applied_once:
            return StepOutcome(
                ok=False,
                summary=("Purchased Parts List: this preview was already "
                         "applied — re-run step 4 for a fresh one."),
                lines=[("  [warn] Already applied once this run — nothing "
                        "submitted again.", TAG_WARN)],
            )
        applied_once = True
        try:
            applied = sync(dry_run=False)
        except Exception as exc:  # noqa: BLE001 — a raise mid-write must not
            # erase the record of how many rows the preview said were
            # coming, and must not overclaim how many landed before it.
            msg = (f"Purchased Parts List: the write failed partway "
                   f"through (expected {expected} new part(s)): {exc}. It "
                   f"may have added some of those {expected} part(s) "
                   f"before failing — re-run the preview to see the "
                   f"current state.")
            return StepOutcome(ok=False, summary=msg,
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])

        # A None/blank result here is the same absent-data problem as the
        # dry run above — applied.get(...) would otherwise raise.
        if not applied:
            msg = (f"Purchased Parts List: the write returned no result "
                   f"(expected {expected} of {expected} new part(s)) — "
                   f"treating as failed since it cannot be confirmed.")
            return StepOutcome(ok=False, summary=msg,
                               lines=[(f"  [fail] {msg}", TAG_FAIL)])

        created = applied.get("created", 0)
        updated = applied.get("updated", 0)
        errors = applied.get("errors") or []
        # R2: 'created' is judged against 'expected', not against itself —
        # a partial write (fewer created than the preview promised) must
        # fail even when the errors list happens to be empty.
        ok = created == expected and not errors
        out = [(f"  [{'ok' if ok else 'fail'}] Added {created} of {expected} "
                f"part(s) to the list.", TAG_PASS if ok else TAG_FAIL)]
        if updated:
            out.append((f"  [ok] Updated {updated} existing part(s).",
                        TAG_PASS))
        for e in errors:
            out.append((f"    [fail] {e.get('name')}: {e.get('error')}",
                        TAG_FAIL))
        summary = (f"Purchased Parts List: added {created} of {expected}, "
                   f"{len(errors)} failed.")
        if updated:
            summary += f" {updated} updated."
        return StepOutcome(ok=ok, summary=summary, lines=out)

    return StepOutcome(
        ok=True,
        summary=(f"{len(missing)} part(s) missing from the list — click "
                 f"Apply to add."),
        lines=lines, pending_apply=apply,
    )


def _publish_deps():
    """Return (scan_bom, submit_jobs) from the publish_bom engine."""
    import publish_bom
    return publish_bom.scan_bom, publish_bom.submit_jobs


def run_publish_deliverables(
    api: Any, vault_id: str, bom_path: str, *, top_assembly: str = "",
) -> StepOutcome:
    """Step 5 — queue PDF and STEP publish jobs for every Make part.

    The scan reports Make parts with no drawing as gaps. The job server
    publishes a PDF *from* an existing drawing; it cannot author one, so
    those gaps are reported and never fixed here.
    """
    import asyncio

    scan_bom, submit_jobs = _publish_deps()

    rows, error = asyncio.run(
        scan_bom(api, vault_id, bom_path, top_assembly=top_assembly))
    if error:
        return StepOutcome(ok=False, summary=error,
                           lines=[(f"  [fail] {error}", TAG_FAIL)])

    total_jobs = sum(r.job_count for r in rows)
    gaps = [r for r in rows if r.job_count < 2]

    lines = [(f"  Scanned {len(rows)} part(s) — {total_jobs} job(s) to queue.",
              TAG_INFO)]
    for r in gaps:
        lines.append((f"      ! {r.stem:20s} {r.status}", TAG_WARN))
    if gaps:
        lines.append(("  Gaps are reported, never created — the job server "
                      "cannot author a missing drawing.", TAG_DIM))

    # R1/R3: an empty (or fully-unresolved) scan is not automatically
    # success. A BOM with genuinely no Make parts and one whose parts all
    # failed to resolve in Vault can both reach here with rows possibly
    # empty and error None — total_jobs is 0 either way, and 0 queued must
    # never stage an apply that would submit nothing and call it done.
    if not total_jobs:
        if not rows:
            summary = ("Nothing to publish — the scan resolved no parts "
                       "(no Make parts in the BOM, or nothing found in "
                       "Vault).")
        else:
            summary = (f"Nothing to publish — no resolved files among "
                       f"{len(rows)} part(s).")
        return StepOutcome(ok=False, summary=summary, lines=lines)

    # Captured here, not re-derived inside apply() (R6): the job count the
    # preview promised is what apply's own report gets judged against.
    applied_once = False

    def apply() -> StepOutcome:
        nonlocal applied_once
        if applied_once:
            return StepOutcome(
                ok=False,
                summary=("Publish Deliverables: this preview was already "
                         "applied — re-run step 5 for a fresh one."),
                lines=[("  [warn] Already applied once this run — nothing "
                        "submitted again.", TAG_WARN)],
            )
        applied_once = True
        try:
            result = asyncio.run(submit_jobs(api, vault_id, rows))
        except Exception as exc:  # noqa: BLE001 — the whole submit call
            # failing must not erase the job count the preview promised.
            msg = (f"Publish Deliverables: the submit call failed before "
                   f"confirming any result (expected {total_jobs} job(s)): "
                   f"{exc}")
            return StepOutcome(ok=False, summary=msg,
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])

        submitted = result.get("submitted")
        failed = result.get("failed", 0)
        # A missing 'submitted' key means the job server told us nothing
        # verifiable — same treatment as step 3's missing 'updated' count.
        if submitted is None:
            msg = (f"Publish Deliverables: the job server did not report "
                   f"how many job(s) were submitted (expected {total_jobs}) "
                   f"— treating as failed since it cannot be confirmed.")
            return StepOutcome(
                ok=False, summary=msg,
                lines=[(f"  [fail] No 'submitted' count in the response "
                        f"(expected {total_jobs}).", TAG_FAIL)])

        ok = submitted == total_jobs and not failed
        return StepOutcome(
            ok=ok,
            summary=(f"Publish Deliverables: {submitted} of {total_jobs} "
                     f"job(s) queued, {failed} failed."),
            lines=[(f"  [{'ok' if ok else 'fail'}] Queued {submitted} of "
                    f"{total_jobs} job(s), {failed} failed.",
                    TAG_PASS if ok else TAG_FAIL)],
        )

    return StepOutcome(
        ok=True,
        summary=(f"{total_jobs} job(s) ready across {len(rows)} part(s), "
                 f"{len(gaps)} gap(s) — click Apply to queue."),
        lines=lines, pending_apply=apply,
    )


def _generate_sheet(**kwargs: Any) -> dict[str, Any]:
    """Thin seam over bom_purchasing.generate_from_file, for testing."""
    import bom_purchasing
    return bom_purchasing.generate_from_file(**kwargs)


def run_purchasing_sheet(
    bom_path: str, assembly_number: str, *, output_dir: str = "",
) -> StepOutcome:
    """Step 6 — build the branded purchasing workbook.

    Deliberately has no apply gate: it writes one .xlsx to disk and touches
    neither Vault nor SharePoint. ``output_dir`` defaults to the BOM's own
    folder.
    """
    result = _generate_sheet(
        bom_file_path=bom_path, assembly_number=assembly_number,
        output_dir=output_dir,
    )
    if result.get("error"):
        msg = result.get("message", "sheet generation failed")
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    path = result.get("output_path") or ""
    # R1: the generator returning without raising is not itself success — a
    # blank output_path, or one that was never actually written to disk,
    # must read as failed rather than as a green "wrote the file" outcome.
    if not path or not os.path.isfile(path):
        msg = (f"Purchasing Sheet generation returned without an error, "
               f"but no workbook exists at {path or '(no path given)'} — "
               f"treating as failed.")
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    matched = result.get("matched_parts", 0)
    total = result.get("total_purchased_parts", 0)
    unmatched = result.get("unmatched_parts") or []
    warnings = result.get("warnings") or []

    lines = [(f"  [ok] Wrote {path}", TAG_PASS),
             (f"  {matched}/{total} purchased part(s) matched the reference "
              f"file.", TAG_INFO)]
    for part in unmatched:
        lines.append((f"      ? {part}  (no reference match)", TAG_WARN))
    for w in warnings:
        lines.append((f"  [warn] {w}", TAG_WARN))

    return StepOutcome(
        ok=True,
        summary=f"Purchasing Sheet written: {path}",
        lines=lines, result=result,
    )


def _names_by_version_id(compliance: dict[str, Any]) -> dict[str, str]:
    """Map file-version id -> display name, for readable log lines."""
    names: dict[str, str] = {}
    info = compliance.get("info") or {}
    top_id = str(info.get("file_version_id") or "")
    if top_id:
        names[top_id] = str(compliance.get("file_name") or top_id)
    for child in compliance.get("children") or []:
        cid = str(child.get("file_version_id") or "")
        if cid:
            names[cid] = str(child.get("file_name") or cid)
    return names
