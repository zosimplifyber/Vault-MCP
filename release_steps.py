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
    callers filter."""
    out = [compliance.get("info") or {}]
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


def unresolved_files(compliance: dict[str, Any]) -> list[str]:
    """Files carrying no usable Vault ID — dropped from a derived list.

    ``file_version_ids`` and ``file_master_ids`` both drop bad entries
    silently, by design (a bad ID would fail the whole batch). Steps 2 and 3
    must surface what got dropped in their preview: a file silently missing
    from a lifecycle batch is a partial release reported as success.

    Flags an entry if EITHER its version ID or its master ID is unusable —
    a file can be fine for Sync (has a version ID) and still vanish from
    Release (no master ID), and that half-visibility is exactly what this
    exists to catch. Returns ``[]`` for a falsy ``compliance`` — with no
    step 1 result there is nothing to report as dropped.
    """
    if not compliance:
        return []
    names: list[str] = []
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
        names.append(str(entry.get("file_name") or "(unnamed)"))
    return names
