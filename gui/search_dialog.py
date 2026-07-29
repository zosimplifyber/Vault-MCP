"""
Modal Vault **item** search dialog.

Its consumer is ``gui/mfg_package.py`` — the MFG Order Package builder, which
genuinely wants to pick an *item* and get a *part number* back. This module is
deliberately item-based: it calls ``api.search_items`` and hands the chosen
row's Number to ``parent.set_part_number``.

The release wizard does **not** use this dialog. It works from file names and
has its own ``FileSearchDialog`` (``gui/release_workflow.py``). This class was
extracted from ``gui/release_workflow.py`` verbatim so that rewriting the
wizard cannot change MFG Order Package's behaviour — do not repoint it at
``search_files`` or otherwise "modernise" it on the wizard's behalf.

The parent GUI must supply this duck-typed contract:

    parent.root, parent.api, parent.vault_id, parent.pn_var,
    parent._brand_button, parent._ensure_signed_in, parent.set_part_number
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui.theme import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, WHITE,
)


class SearchDialog:
    """Modal Vault item search — query box, results table, double-click to pick.

    Reuses the parent GUI's authenticated ``VaultRestAPI`` client (signs in
    lazily via ``parent._ensure_signed_in`` if no session yet). All search
    work runs on a worker thread so the dialog never freezes; results are
    posted back to the UI via ``parent.root.after``.
    """

    COLUMNS = [
        ("number",      "Number",      120),
        ("title",       "Title",       180),
        ("description", "Description", 320),
        ("revision",    "Rev",          50),
        ("state",       "State",       110),
        ("category",    "Category",    180),
    ]

    def __init__(self, parent_gui: Any) -> None:
        self.parent = parent_gui
        self.results: list[dict[str, Any]] = []
        self.busy = False
        # Cross-thread queue — Tk widget calls must happen on the Tk thread,
        # so the worker posts results here and a Tk-thread drain consumes them.
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._build_window()
        # Pre-fill with whatever's already in the main Part Number entry so
        # the typical "search for what I have" flow is one click + Enter.
        existing = parent_gui.pn_var.get().strip()
        if existing:
            self.query_var.set(existing)
        self.query_entry.focus_set()
        self.parent.root.after(100, self._drain_queue)

    # ----- Window construction ---------------------------------------------

    def _build_window(self) -> None:
        self.win = tk.Toplevel(self.parent.root)
        self.win.title("Search Vault")
        self.win.geometry("880x520")
        self.win.minsize(640, 360)
        self.win.configure(bg=LIGHT_GRAY)
        self.win.transient(self.parent.root)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Header strip — same dark blue / mid blue treatment as the main window
        hdr = tk.Frame(self.win, bg=DARK_BLUE, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="  Search Vault",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 12, "bold"),
            anchor="w", padx=12,
        ).pack(side="left", fill="y")
        tk.Frame(self.win, bg=MID_BLUE, height=2).pack(fill="x")

        # Query bar
        bar = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=12)
        bar.pack(fill="x")
        tk.Label(
            bar, text="Query",
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            font=("Arial", 9, "bold"),
        ).pack(side="left", padx=(0, 6))

        self.query_var = tk.StringVar()
        self.query_entry = tk.Entry(
            bar, textvariable=self.query_var,
            font=("Arial", 10),
            bg=WHITE, relief="solid", bd=1,
            highlightthickness=1,
            highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        )
        self.query_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.query_entry.bind("<Return>", lambda _e: self._do_search())

        self.search_btn = self.parent._brand_button(
            bar, "Search", self._do_search, primary=True,
        )
        self.search_btn.pack(side="left", padx=(0, 6))

        self.limit_var = tk.StringVar(value="50")
        tk.Label(
            bar, text="Limit",
            bg=LIGHT_GRAY, fg=DARK_BLUE, font=("Arial", 9),
        ).pack(side="left", padx=(8, 4))
        tk.Entry(
            bar, textvariable=self.limit_var, width=5,
            font=("Arial", 10), bg=WHITE,
            relief="solid", bd=1,
            highlightthickness=1, highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        ).pack(side="left")

        # Results table — ttk.Treeview with Simplifyber-styled headers
        body = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=0)
        body.pack(fill="both", expand=True, pady=(0, 10))

        style = ttk.Style(self.win)
        # 'clam' is the only built-in ttk theme that respects every colour
        # option on Treeview headings — the native Windows theme ignores them.
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Vault.Treeview",
            background=WHITE, fieldbackground=WHITE,
            foreground="#222222",
            rowheight=24, borderwidth=0,
            font=("Arial", 10),
        )
        style.configure(
            "Vault.Treeview.Heading",
            background=DARK_BLUE, foreground=WHITE,
            font=("Arial", 10, "bold"),
            relief="flat",
        )
        style.map(
            "Vault.Treeview",
            background=[("selected", MID_BLUE)],
            foreground=[("selected", WHITE)],
        )
        style.map(
            "Vault.Treeview.Heading",
            background=[("active", MID_BLUE)],
        )

        col_ids = [c[0] for c in self.COLUMNS]
        self.tree = ttk.Treeview(
            body, columns=col_ids, show="headings",
            style="Vault.Treeview", selectmode="browse",
        )
        for cid, label_text, width in self.COLUMNS:
            self.tree.heading(cid, text=label_text)
            self.tree.column(cid, width=width, anchor="w", stretch=True)
        self.tree.tag_configure("alt", background=PALE_BLUE)

        ys = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda _e: self._on_use_selected())
        self.tree.bind("<Return>",   lambda _e: self._on_use_selected())

        # Action bar
        actions = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=10)
        actions.pack(fill="x")
        self.use_btn = self.parent._brand_button(
            actions, "Use selected", self._on_use_selected, primary=True,
        )
        self.use_btn.pack(side="left", padx=(0, 8))
        self.parent._brand_button(
            actions, "Cancel", self._on_cancel, primary=False,
        ).pack(side="left")

        # Status bar
        self.status_var = tk.StringVar(
            value="Enter a query (part number, title, or keyword) and press Enter."
        )
        bar2 = tk.Frame(self.win, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        bar2.pack(fill="x", side="bottom")
        tk.Label(
            bar2, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 9), anchor="w",
            padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    # ----- Search execution -------------------------------------------------

    def _do_search(self) -> None:
        if self.busy:
            return
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning(
                "Missing query", "Type a part number or keyword to search for.",
                parent=self.win,
            )
            return
        try:
            limit = max(1, int(self.limit_var.get().strip() or "50"))
        except ValueError:
            limit = 50

        self.busy = True
        self.search_btn.configure(state="disabled")
        self.status_var.set(f"Searching for {query!r} …")
        self._clear_results()

        def worker() -> None:
            try:
                if not self.parent._ensure_signed_in():
                    self._post_done(error="Vault sign-in failed — see main window log.")
                    return
                resp = asyncio.run(self.parent.api.search_items(
                    vault_id=self.parent.vault_id,
                    query=query,
                    limit=limit,
                ))
            except Exception as exc:  # noqa: BLE001
                self._post_done(error=f"{type(exc).__name__}: {exc}")
                return
            if resp.get("error"):
                self._post_done(error=str(resp.get("data")))
                return
            rows = self._extract_rows(resp.get("data"))
            self._post_done(rows=rows, query=query)

        threading.Thread(target=worker, daemon=True).start()

    def _post_done(self, *, rows: Optional[list[dict[str, Any]]] = None,
                   query: str = "", error: str = "") -> None:
        # Hop back onto the Tk thread before touching widgets — the drain
        # loop (running under root.after) calls _render_done from the right
        # thread. Calling root.after directly from a worker is unsafe.
        self.q.put(("done", (rows or [], query, error)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "done":
                    rows, query, error = payload
                    self._render_done(rows, query, error)
        except queue.Empty:
            pass
        # Re-arm only while the dialog window is alive
        try:
            if self.win.winfo_exists():
                self.parent.root.after(100, self._drain_queue)
        except tk.TclError:
            pass

    def _render_done(self, rows: list[dict[str, Any]], query: str, error: str) -> None:
        self.busy = False
        self.search_btn.configure(state="normal")
        if error:
            self.status_var.set(f"Search failed: {error}")
            return
        self.results = rows
        self._populate_tree(rows)
        if rows:
            self.status_var.set(
                f"{len(rows)} result(s) for {query!r}. "
                "Double-click or 'Use selected' to pick one."
            )
            # Pre-select the first row for keyboard-only flow
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
        else:
            self.status_var.set(f"No results for {query!r}.")

    # ----- Result extraction & rendering -----------------------------------

    @staticmethod
    def _extract_rows(data: Any) -> list[dict[str, Any]]:
        """Pull a list of item records out of the search response."""
        items: list[dict[str, Any]] = []
        if data is None:
            return items
        if isinstance(data, list):
            items = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("results", "items", "itemVersions",
                        "data", "value", "records"):
                inner = data.get(key)
                if isinstance(inner, list):
                    items = [r for r in inner if isinstance(r, dict)]
                    break
            else:
                if data.get("id") or data.get("masterId"):
                    items = [data]

        rows: list[dict[str, Any]] = []
        for it in items:
            rows.append(_summarise_item_for_search(it))
        return rows

    def _populate_tree(self, rows: list[dict[str, Any]]) -> None:
        for i, row in enumerate(rows):
            tags = ("alt",) if i % 2 == 1 else ()
            values = [row.get(cid, "") for cid, *_ in self.COLUMNS]
            self.tree.insert("", "end", iid=str(i), values=values, tags=tags)

    def _clear_results(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.results = []

    # ----- Use / cancel ----------------------------------------------------

    def _on_use_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "No selection", "Pick a row first.", parent=self.win)
            return
        idx = int(sel[0])
        row = self.results[idx]
        number = str(row.get("number") or "").strip()
        if not number:
            messagebox.showwarning(
                "No part number",
                "Selected row has no Number — pick a different result.",
                parent=self.win,
            )
            return
        self.parent.set_part_number(number)
        self._close()

    def _on_cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


def _summarise_item_for_search(record: dict[str, Any]) -> dict[str, str]:
    """Pick out the small set of fields the search dialog displays.

    Vault returns item records in a couple of different shapes (flat at the
    root vs. nested under ``itemVersion``); this normalises both into the
    short string dict the Treeview expects.
    """
    def pick(*keys, default: str = "") -> str:
        for k in keys:
            v = record.get(k)
            if v not in (None, ""):
                return str(v)
        # Fall back to the embedded latest version if present
        for vkey in ("itemVersion", "latestItemVersion", "latestVersion"):
            inner = record.get(vkey)
            if isinstance(inner, dict):
                for k in keys:
                    v = inner.get(k)
                    if v not in (None, ""):
                        return str(v)
        return default

    return {
        "number":      pick("number", "Number", "itemNumber", "partNumber"),
        "title":       pick("title", "Title (Item,CO)", "name"),
        "description": pick("description", "Description (Item,CO)", "desc"),
        "revision":    pick("revision", "Revision", "revisionNumber"),
        "state":       pick("state", "State", "lifecycleState"),
        "category":    pick("category", "Category Name",
                            "categoryName", "itemCategory"),
    }
