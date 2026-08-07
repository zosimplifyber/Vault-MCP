"""
Shared Tk widgets and helpers for the Simplifyber GUIs.

Everything here had been copy-pasted between two or three GUI modules before
it was extracted. It lives in its own module rather than in ``gui.theme``
because ``theme`` is deliberately toolkit-free apart from an optional Pillow
import -- ``tests/test_release_steps.py`` imports it just to assert the
palette, and that assertion should not start requiring tkinter.

These are generic widget concerns, not domain logic, so sharing them does not
couple the GUIs to each other.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path

from gui.theme import DARK_BLUE, GRAY_BDR, LIGHT_GRAY, MID_BLUE, WHITE


def card(parent, title: str, *, bg: str = WHITE, pady=(0, 10), padx: int = 18):
    """A bordered panel with the brand's dark-blue caption bar. Returns its body."""
    frame = tk.Frame(parent, bg=bg, highlightthickness=1,
                     highlightbackground=GRAY_BDR)
    frame.pack(fill="x", padx=padx, pady=pady)
    tk.Label(frame, text=f"  {title}", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(frame, bg=MID_BLUE, height=2).pack(fill="x")
    body = tk.Frame(frame, bg=bg, padx=14, pady=10)
    body.pack(fill="both", expand=True)
    return body


def build_scroll_area(parent, *, bg: str = LIGHT_GRAY, pady: int = 0) -> tk.Frame:
    """A vertically-scrollable region. Returns the inner frame to fill.

    Pack this after any header and before any bottom-packed status bar, so it
    takes the space that is left.

    The fiddly parts are the scrollregion recompute, keeping the inner frame
    as wide as the canvas, and binding the wheel only while the pointer is
    over this area -- without that last part a child window steals the
    parent's scrolling. Worth having in one place.
    """
    outer = tk.Frame(parent, bg=bg)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, bg=bg, highlightthickness=0)
    vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg=bg, pady=pady)
    win_id = canvas.create_window((0, 0), window=content, anchor="nw")

    content.bind("<Configure>",
                 lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

    def _on_canvas_configure(event):
        # Match the inner frame to the canvas width, and stretch it when the
        # window is taller than the content so the last card's background
        # fills the viewport instead of leaving a gap.
        canvas.itemconfigure(win_id, width=event.width)
        canvas.itemconfigure(
            win_id, height=max(content.winfo_reqheight(), event.height))
    canvas.bind("<Configure>", _on_canvas_configure)

    def _on_wheel(event):
        canvas.yview_scroll(int(-event.delta / 120), "units")
    canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
    canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

    content.scroll_canvas = canvas       # some callers want to scroll it
    return content


def open_in_file_manager(path: str | Path) -> None:
    """Reveal a file or folder in the OS file manager.

    Three-way on ``sys.platform``, not ``os.startfile`` with an AttributeError
    fallback: startfile is missing on macOS too, and falling through to
    ``xdg-open`` there sends it at a command macOS does not have.

    Raises whatever the platform call raises -- callers surface it, since the
    right message differs per screen.
    """
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
