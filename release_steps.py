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


@dataclass
class StepOutcome:
    """What a step reports back to the wizard.

    ``pending_apply`` is the whole preview-then-write mechanism: when it is
    set, the step has computed a preview and staged a write that has NOT
    happened. The wizard renders ``lines``, moves the step to REVIEW, and waits
    for a click. Calling it performs the write and returns the final outcome.
    """
    ok: bool
    summary: str
    lines: list[tuple[str, str]] = field(default_factory=list)
    pending_apply: Optional[Callable[[], "StepOutcome"]] = None
    result: Any = None


def _child_failed(child: dict[str, Any]) -> bool:
    if child.get("error"):
        return True
    return bool((child.get("report") or {}).get("failed", 0))


def property_check_blocked(
    compliance: Optional[dict[str, Any]], *, force: bool
) -> Optional[str]:
    """Return a reason string when Sync / Release must not run, else None.

    Only steps 2 and 3 consult this. The BOM steps are deliberately not gated:
    a purchasing sheet is useful while properties are still being fixed.

    ``force`` covers failing properties. It deliberately does NOT cover a
    missing result — steps 2 and 3 take their file list from step 1, so with no
    step 1 there is no work to force past.
    """
    if not compliance:
        return "Run step 1 (Property Check) first — it supplies the file list."
    if force:
        return None
    failed = bool((compliance.get("report") or {}).get("failed", 0))
    kids = any(_child_failed(c) for c in (compliance.get("children") or []))
    if failed or kids:
        return (
            "Property Check found failures. Fix them and re-run step 1, or "
            "tick 'Force past compliance gate' to continue anyway."
        )
    return None


def _entries(compliance: dict[str, Any]) -> list[dict[str, Any]]:
    """Top file first, then every CAD BOM child that actually resolved."""
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
            logger.debug("Skipping unparseable file master id %r", raw)
            continue
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids
