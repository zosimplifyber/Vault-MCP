"""
Inventor COM automation helpers used by the release workflow.

Drives a running (or freshly launched) Inventor instance to:
  * open an assembly from a local working-folder path
  * trigger the Vault add-in to "Get Latest" on the file (so all referenced
    parts are pulled local before the rebuild)
  * rebuild / Update2 the assembly
  * save the assembly (which lets the Vault add-in mark it dirty for check-in)

Why COM and not REST?
---------------------
The Vault REST v2 API can download file *bytes* directly, but it cannot
restore a working-folder layout (relative paths, library paths, custom
content centre links) the way the Vault add-in does. The Inventor add-in
also handles linked-file resolution. So for any release that needs the
assembly to actually open and rebuild correctly, we drive Inventor.

Requires `pywin32` (`pip install pywin32`) and a local Inventor install.
The module is import-safe on machines without Inventor — every entry point
raises a clear `InventorUnavailableError` if the COM object cannot be
created, so the workflow can degrade to "open this assembly manually".
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class InventorUnavailableError(RuntimeError):
    """Raised when Inventor / pywin32 is not available on this machine."""


class InventorAutomationError(RuntimeError):
    """Raised when an Inventor COM operation fails."""


# ---------------------------------------------------------------------------
# COM bootstrap
# ---------------------------------------------------------------------------

def _import_win32():
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
        return pythoncom, win32com.client
    except ImportError as exc:
        raise InventorUnavailableError(
            "pywin32 is not installed. Install it with: pip install pywin32"
        ) from exc


def get_inventor_app(*, visible: bool = True):
    """Return a live Inventor.Application COM object.

    Tries to attach to a running Inventor first; falls back to launching a
    new instance. Raises ``InventorUnavailableError`` if Inventor isn't
    installed and ``InventorAutomationError`` for any COM failure.
    """
    pythoncom, win32com_client = _import_win32()

    try:
        # Prefer attaching to an already-open Inventor — faster, and avoids
        # spawning a second process that won't share the user's session.
        app = win32com_client.GetActiveObject("Inventor.Application")
        logger.info("Attached to running Inventor.Application")
    except pythoncom.com_error:
        try:
            app = win32com_client.Dispatch("Inventor.Application")
            logger.info("Started new Inventor.Application")
        except pythoncom.com_error as exc:
            raise InventorUnavailableError(
                f"Could not start Inventor.Application: {exc}. "
                "Is Autodesk Inventor installed on this machine?"
            ) from exc

    try:
        app.Visible = visible
    except Exception:  # noqa: BLE001
        # Some Inventor licences forbid hiding the UI; ignore.
        pass

    return app


# ---------------------------------------------------------------------------
# Assembly operations
# ---------------------------------------------------------------------------

@contextmanager
def open_document(app, file_path: str | Path, *, save_on_close: bool = False) -> Iterator:
    """Open ``file_path`` in Inventor and yield the resulting Document object.

    The document is closed automatically on exit. Pass ``save_on_close=True``
    to commit changes (caller is responsible for triggering `update`/save).
    """
    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        raise InventorAutomationError(f"File does not exist: {p}")

    logger.info("Inventor: opening %s", p)
    try:
        doc = app.Documents.Open(str(p), True)  # OpenVisible = True
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(f"Documents.Open failed for {p}: {exc}") from exc

    try:
        yield doc
    finally:
        try:
            logger.info("Inventor: closing %s (save=%s)", p, save_on_close)
            doc.Close(not save_on_close)  # SkipSave = inverse of save_on_close
        except Exception as exc:  # noqa: BLE001
            logger.warning("Document.Close failed: %s", exc)


def rebuild_document(doc) -> None:
    """Force a full rebuild (Update2) on the document.

    Inventor's Update2(True) re-evaluates every feature in the model and is
    the rebuild equivalent of the user pressing "Update" in the ribbon.
    """
    logger.info("Inventor: rebuild (Update2) — %s", _doc_name(doc))
    try:
        doc.Update2(True)
    except AttributeError:
        # Older API — fall back to Update()
        doc.Update()
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(f"Update2 failed: {exc}") from exc


def save_document(doc) -> None:
    """Save the document in place (no SaveAs). The Vault add-in will pick
    this up as a dirty file ready for check-in."""
    logger.info("Inventor: save — %s", _doc_name(doc))
    try:
        doc.Save()
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(f"Document.Save failed: {exc}") from exc


def _doc_name(doc) -> str:
    try:
        return str(doc.FullFileName)
    except Exception:  # noqa: BLE001
        return "<document>"


# ---------------------------------------------------------------------------
# Vault add-in commands
# ---------------------------------------------------------------------------

# Internal command IDs registered by the Inventor Vault add-in. These have
# been stable for many Inventor releases; if a particular install renames
# them the call raises and we surface a clear error.
_VAULT_GET_LATEST_CMD = "Connectivity.VaultAddinServer.GetCommand"
_VAULT_CHECK_IN_CMD   = "Connectivity.VaultAddinServer.CheckinCommand"


def _execute_command(app, command_id: str) -> None:
    try:
        cmd = app.CommandManager.ControlDefinitions.Item(command_id)
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(
            f"Vault add-in command not found: {command_id}. "
            "Is the Inventor Vault add-in loaded?"
        ) from exc
    try:
        cmd.Execute()
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(f"Command {command_id} failed: {exc}") from exc


def vault_get_latest(app) -> None:
    """Trigger the Vault add-in's Get Latest on the active document.

    Note: this opens the Vault add-in dialog modally — the workflow will
    block until the user completes it. There is no headless equivalent
    exposed to COM.
    """
    logger.info("Inventor: dispatching Vault add-in 'Get Latest' command")
    _execute_command(app, _VAULT_GET_LATEST_CMD)


def vault_check_in(app) -> None:
    """Trigger the Vault add-in's Check In command for the active document.

    Like ``vault_get_latest`` this is modal — workflow blocks until the
    Vault dialog is dismissed.
    """
    logger.info("Inventor: dispatching Vault add-in 'Check In' command")
    _execute_command(app, _VAULT_CHECK_IN_CMD)


# ---------------------------------------------------------------------------
# Convenience: full rebuild-and-save for a single assembly
# ---------------------------------------------------------------------------

def rebuild_and_save_assembly(
    file_path: str | Path,
    *,
    visible: bool = True,
    settle_seconds: float = 1.0,
) -> str:
    """Open → rebuild → save an assembly. Returns the absolute path used.

    Raises ``InventorUnavailableError`` if Inventor / pywin32 isn't
    available, ``InventorAutomationError`` for any operation failure.
    """
    app = get_inventor_app(visible=visible)
    with open_document(app, file_path, save_on_close=True) as doc:
        # Brief settle so the Vault add-in finishes attaching to the doc
        time.sleep(settle_seconds)
        rebuild_document(doc)
        save_document(doc)
    return str(Path(file_path).expanduser().resolve())


__all__ = [
    "InventorUnavailableError",
    "InventorAutomationError",
    "get_inventor_app",
    "open_document",
    "rebuild_document",
    "save_document",
    "vault_get_latest",
    "vault_check_in",
    "rebuild_and_save_assembly",
]
