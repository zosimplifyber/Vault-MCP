"""
Renders the Formed Fiber design-to-process handoff as a one-page PDF.

Purely visual: rows come in already rendered by ``formed_fiber_handoff`` --
target markers applied, blanks turned into em dashes -- so nothing in here
decides what a field says, only how it looks. Layout tweaks cannot change a
number.

Colours come from ``gui.theme``, which is the canonical palette. Note that
``bom_purchasing.py`` keeps its own copy without the leading '#' because
openpyxl demands that form; the two are kept in sync by hand.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from formed_fiber_handoff import (
    HandoffData, file_rows, machine_rows, production_rows,
)
from gui.theme import (
    DARK_BLUE, GRAY_BDR, DARK_GRAY, PALE_BLUE, PROJECT_ROOT,
)

BRAND_BLUE = colors.HexColor(DARK_BLUE)
BAND_BLUE = colors.HexColor(PALE_BLUE)
GRID_GRAY = colors.HexColor(GRAY_BDR)
NOTE_GRAY = colors.HexColor(DARK_GRAY)

PAGE_MARGIN = 0.62 * inch
CONTENT_WIDTH = letter[0] - (2 * PAGE_MARGIN)

INTRO = (
    "This document transfers the design data for a formed fiber part to the "
    "process team responsible for running the mold, so the parameters "
    "established during development carry forward to the press without loss. "
    "Complete every field at handoff, and mark any value that is a target "
    "rather than a measured result."
)

SECTIONS = (
    (
        "1. Machine and Process Details",
        "Identify the press the mold will run on. If the machine has not "
        "already been characterized, it must be characterized before the "
        "first production run.",
        machine_rows,
    ),
    (
        "2. Production Details",
        "Record the values established during development. For a new part, "
        "record the target values the process is to be set up against.",
        production_rows,
    ),
    (
        "3. File References",
        "Record filenames exactly as released, including revision, so the "
        "process team can pull the correct geometry.",
        file_rows,
    ),
)

CONFIDENTIALITY = (
    "The information contained in these documents is confidential, privileged "
    "and intended solely for the information of the intended recipient and may "
    "not be used, published or redistributed without the prior written consent "
    "of Simplifyber, Inc."
)


def _logo_path() -> Path:
    """Where the brand logo lives. A function so tests can point it away."""
    return PROJECT_ROOT / "Simplifyber_Logo.png"


# ----------------------------------------------------------------- styles

_TITLE = ParagraphStyle(
    "handoffTitle", fontName="Helvetica", fontSize=16, leading=20,
    textColor=BRAND_BLUE, spaceAfter=8,
)
_INTRO = ParagraphStyle(
    "handoffIntro", fontName="Helvetica", fontSize=9.5, leading=13.5,
    textColor=colors.black, spaceAfter=10,
)
_HEADING = ParagraphStyle(
    "handoffHeading", fontName="Helvetica-Bold", fontSize=12, leading=15,
    textColor=BRAND_BLUE, spaceBefore=6, spaceAfter=2,
)
_LEAD_IN = ParagraphStyle(
    "handoffLeadIn", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5,
    textColor=NOTE_GRAY, spaceBefore=4, spaceAfter=6,
)
_FOOTER = ParagraphStyle(
    "handoffFooter", fontName="Helvetica-Oblique", fontSize=6.5, leading=8.5,
    textColor=colors.black,
)


def _rule(width: float, thickness: float = 1.2, color=BRAND_BLUE) -> Table:
    """A horizontal rule, drawn as a one-cell table so it flows."""
    line = Table([[""]], colWidths=[width], rowHeights=[thickness])
    line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return line


def _parameter_table(rows: list[tuple[str, str]]) -> Table:
    """The PARAMETER / VALUE table used by all three sections."""
    body = [["PARAMETER", "VALUE"]] + [[name, value] for name, value in rows]
    table = Table(body, colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.55],
                  hAlign="LEFT")

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (0, -1), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # Band every other data row, matching the source document.
    for index in range(1, len(body)):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), BAND_BLUE))
    table.setStyle(TableStyle(style))
    return table


def _page_furniture(canvas, doc, data: HandoffData) -> None:
    """Header and footer -- page furniture, so drawn rather than flowed."""
    canvas.saveState()
    width, height = letter
    top = height - PAGE_MARGIN

    logo = _logo_path()
    drew_logo = False
    if logo.is_file():
        try:
            canvas.drawImage(str(logo), PAGE_MARGIN, top - 24,
                             width=138, height=26,
                             preserveAspectRatio=True, anchor="sw", mask="auto")
            drew_logo = True
        except Exception:  # noqa: BLE001
            drew_logo = False
    if not drew_logo:
        # Same degradation the GUIs use when Pillow or the asset is absent.
        canvas.setFont("Helvetica-Bold", 13)
        canvas.setFillColor(BRAND_BLUE)
        canvas.drawString(PAGE_MARGIN, top - 18, "SIMPLIFYBER")

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(NOTE_GRAY)
    canvas.drawRightString(width - PAGE_MARGIN, top - 14, "Page 1 of 1")

    canvas.setStrokeColor(GRID_GRAY)
    canvas.setLineWidth(0.6)
    canvas.line(PAGE_MARGIN, top - 32, width - PAGE_MARGIN, top - 32)

    # Footer
    footer_top = PAGE_MARGIN + 46
    canvas.line(PAGE_MARGIN, footer_top, width - PAGE_MARGIN, footer_top)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.black)
    # Built from the parts rather than strftime: the document's format is
    # 8/6/2026, and the strftime code for an unpadded number differs between
    # platforms (%-m on Linux, %#m on Windows). This works everywhere.
    stamp = data.generated_on
    canvas.drawString(PAGE_MARGIN, footer_top - 12,
                      f"Date: {stamp.month}/{stamp.day}/{stamp.year}")
    canvas.setFont("Helvetica", 8)
    canvas.drawString(PAGE_MARGIN, footer_top - 23, "CONFIDENTIAL")

    note = Paragraph(CONFIDENTIALITY, _FOOTER)
    note.wrapOn(canvas, CONTENT_WIDTH, 40)
    note.drawOn(canvas, PAGE_MARGIN, footer_top - 44)

    canvas.restoreState()


def render_handoff_pdf(data: HandoffData, output_path: str | Path) -> Path:
    """Write the handoff PDF and return the path written."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=letter,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN + 30,
        bottomMargin=PAGE_MARGIN + 52,
        title="Formed Fiber: Design-to-Process Handoff",
        author="Simplifyber",
    )

    story: list = [
        Paragraph(
            '<b>FORMED FIBER</b>: DESIGN-TO-PROCESS HANDOFF', _TITLE),
        Paragraph(INTRO, _INTRO),
    ]

    for heading, lead_in, build_rows in SECTIONS:
        story.append(KeepTogether([
            Paragraph(heading, _HEADING),
            _rule(CONTENT_WIDTH, 1.0),
            Paragraph(lead_in, _LEAD_IN),
            _parameter_table(build_rows(data)),
            Spacer(1, 12),
        ]))

    def _on_page(canvas, doc_):
        _page_furniture(canvas, doc_, data)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return out
