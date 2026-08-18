"""Assemble a critical-reading corpus from freely published primary sources.

The five books this serves are in-copyright trade books, which are not ours to
copy. What *is* freely published is, in most cases, the material the book was
built from: the author's own statement of the method, the rights holder's free
essay archive, or the university course the book is a write-up of. Those are
what this script collects.

Each source becomes one Markdown document carrying a provenance header, so a
retrieved chunk can always be traced back to who published it and why we may
use it.

    python scripts/fetch_critical_reading_corpus.py

Writes to docs/rag/critical-reading/. Sources that fail are reported and
skipped rather than aborting the run; files no longer in SOURCES are pruned.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "rag" / "critical-reading"

# Several of these hosts 406 a bare requests User-Agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Content shorter than this means the fetch technically succeeded but the page
# was a shell (JS-rendered site, consent wall) with nothing worth embedding.
MIN_USEFUL_CHARS = 1500


@dataclass(frozen=True)
class Source:
    book: str          # the reading-list entry this stands in for
    slug: str          # output filename stem
    title: str
    url: str
    publisher: str     # who publishes it free, i.e. why we may use it
    note: str = ""     # how it maps onto the book
    extractor: str = "auto"   # "auto" | "ted" - sites needing bespoke handling
    min_chars: int = MIN_USEFUL_CHARS  # lower it for pages that are simply short


# Ordered as the reading list is ordered: Minto first, since it pays off fastest.
SOURCES: list[Source] = [
    # ---- 1. The Pyramid Principle - Barbara Minto -------------------------
    Source(
        "1. The Pyramid Principle (Minto)",
        "01-minto-pyramid-concept",
        "The Minto Pyramid Principle: the concept",
        "https://www.barbaraminto.com/concept",
        "Barbara Minto (author's own site)",
        "Minto's own statement of the method: ideas ordered as a pyramid under a "
        "single governing point, each level summarising the level below.",
        min_chars=800,  # genuinely brief, but it is the whole statement
    ),
    Source(
        "1. The Pyramid Principle (Minto)",
        "01-minto-textbook-outline",
        "The Minto Pyramid Principle: book outline",
        "https://www.barbaraminto.com/textbook",
        "Barbara Minto (author's own site)",
        "The book's own structure - useful as a map of what each part argues.",
        min_chars=600,
    ),
    Source(
        "1. The Pyramid Principle (Minto)",
        "01-minto-course-outline",
        "The Minto Pyramid Principle: course outline",
        "https://www.barbaraminto.com/course",
        "Barbara Minto (author's own site)",
        "The taught sequence, including the SCQ (situation-complication-question) "
        "framing and the rules for MECE groupings.",
        min_chars=600,
    ),
    # ---- 2. Thinking in Systems - Donella Meadows ------------------------
    Source(
        "2. Thinking in Systems (Meadows)",
        "02-meadows-leverage-points",
        "Leverage Points: Places to Intervene in a System",
        "https://donellameadows.org/archives/leverage-points-places-to-intervene-in-a-system/",
        "The Donella Meadows Project (rights holder)",
        "The essay the book's final chapters expand: twelve places to intervene, "
        "ranked by how much change each one buys.",
    ),
    Source(
        "2. Thinking in Systems (Meadows)",
        "02-meadows-leverage-points-paper",
        "Leverage Points (full Sustainability Institute paper, PDF)",
        "https://donellameadows.org/wp-content/userfiles/Leverage_Points.pdf",
        "The Donella Meadows Project (rights holder)",
        "The longer paper version, with the worked examples behind each point.",
    ),
    Source(
        "2. Thinking in Systems (Meadows)",
        "02-meadows-dancing-with-systems",
        "Dancing With Systems",
        "https://donellameadows.org/archives/dancing-with-systems/",
        "The Donella Meadows Project (rights holder)",
        "Meadows' rules for acting inside a system you cannot fully model - "
        "the book's closing argument, in essay form.",
    ),
    Source(
        "2. Thinking in Systems (Meadows)",
        "02-meadows-systems-resources",
        "Systems thinking resources",
        "https://donellameadows.org/systems-thinking-resources/",
        "The Donella Meadows Project (rights holder)",
        "The Project's own primer material on stocks, flows, feedback and delays.",
    ),
    Source(
        "2. Thinking in Systems (Meadows)",
        "02-twelve-leverage-points-reference",
        "Twelve leverage points (reference summary)",
        "https://en.wikipedia.org/wiki/Twelve_leverage_points",
        "Wikipedia (CC BY-SA)",
        "A compact enumerated reference for the twelve points - handy for "
        "retrieval when you want the list rather than the argument.",
    ),
    # ---- 3. Calling Bullshit - Bergstrom & West --------------------------
    Source(
        "3. Calling Bullshit (Bergstrom & West)",
        "03-calling-bullshit-syllabus",
        "Calling Bullshit: syllabus and readings",
        "https://www.callingbullshit.org/syllabus.html",
        "Bergstrom & West, University of Washington (free course site)",
        "The book is this course written up; the syllabus is the same skeleton, "
        "lecture by lecture, with the reading behind each one.",
    ),
    Source(
        "3. Calling Bullshit (Bergstrom & West)",
        "03-calling-bullshit-case-studies",
        "Calling Bullshit: case studies",
        "https://www.callingbullshit.org/case_studies.html",
        "Bergstrom & West, University of Washington (free course site)",
        "Worked examples of catching bad claims with no domain expertise.",
    ),
    Source(
        "3. Calling Bullshit (Bergstrom & West)",
        "03-calling-bullshit-tools",
        "Calling Bullshit: tools",
        "https://www.callingbullshit.org/tools.html",
        "Bergstrom & West, University of Washington (free course site)",
        "The reusable checks - Fermi estimation, base rates, selection effects.",
    ),
    Source(
        "3. Calling Bullshit (Bergstrom & West)",
        "03-calling-bullshit-videos",
        "Calling Bullshit: lecture index",
        "https://www.callingbullshit.org/videos.html",
        "Bergstrom & West, University of Washington (free course site)",
        "Lecture-by-lecture index; the topic order matches the book's chapters.",
    ),
    Source(
        "3. Calling Bullshit (Bergstrom & West)",
        "03-calling-bullshit-faq",
        "Calling Bullshit: FAQ",
        "https://www.callingbullshit.org/FAQ.html",
        "Bergstrom & West, University of Washington (free course site)",
        "The authors' own framing of what the method does and does not claim.",
    ),
    # ---- 4. How to Read a Book, Part IV - Adler & Van Doren --------------
    Source(
        "4. How to Read a Book, Part IV (Adler & Van Doren)",
        "04-adler-four-levels-and-syntopical",
        "How to Read a Book: the four levels, including syntopical reading",
        "https://fs.blog/how-to-read-a-book/",
        "Farnam Street (free article)",
        "A detailed treatment of the four levels, with the Part IV syntopical "
        "procedure - bibliography, shared vocabulary, framing the question - "
        "set out step by step.",
    ),
    Source(
        "4. How to Read a Book, Part IV (Adler & Van Doren)",
        "04-how-to-read-a-book-reference",
        "How to Read a Book (structure and argument summary)",
        "https://en.wikipedia.org/wiki/How_to_Read_a_Book",
        "Wikipedia (CC BY-SA)",
        "The book's structure, so you can see where Part IV sits and skip the rest.",
    ),
    Source(
        "4. How to Read a Book, Part IV (Adler & Van Doren)",
        "04-syntopicon-reference",
        "A Syntopicon (Adler's own syntopical apparatus)",
        "https://en.wikipedia.org/wiki/A_Syntopicon",
        "Wikipedia (CC BY-SA)",
        "The index Adler actually built to read syntopically - the worked example "
        "of the Part IV method at scale.",
    ),
    # ---- 5. The Scout Mindset - Julia Galef ------------------------------
    Source(
        "5. The Scout Mindset (Galef)",
        "05-galef-ted-scout-mindset",
        "Why you think you're right - even if you're wrong (transcript)",
        "https://www.ted.com/talks/julia_galef_why_you_think_you_re_right_even_if_you_re_wrong/transcript",
        "TED (free transcript)",
        "Galef's own statement of the soldier/scout distinction the book expands, "
        "including the Dreyfus affair as the worked example.",
        extractor="ted",
    ),
    Source(
        "5. The Scout Mindset (Galef)",
        "05-galef-on-her-book",
        "My new book: The Scout Mindset",
        "https://juliagalef.substack.com/p/my-new-book-the-scout-mindset",
        "Julia Galef (author's own newsletter)",
        "Galef on what the book argues and why - her framing of the thesis.",
    ),
    Source(
        "5. The Scout Mindset (Galef)",
        "05-scout-mindset-reference",
        "The Scout Mindset (summary of the argument)",
        "https://en.wikipedia.org/wiki/The_Scout_Mindset",
        "Wikipedia (CC BY-SA)",
        "Chapter-level summary of the book's structure and claims.",
    ),
]


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]):
        tag.decompose()
    # Drop the usual site furniture by role before taking text.
    for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo"]}):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def clean_pdf(payload: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(payload))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(p for p in pages if p)
    # PDF extraction leaves hyphenated line breaks mid-word.
    return re.sub(r"-\n(\w)", r"\1", text)


def clean_ted(html: str) -> str:
    """TED renders the transcript client-side; the text ships in __NEXT_DATA__."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("TED page has no __NEXT_DATA__ payload")
    data = json.loads(match.group(1))
    paragraphs = data["props"]["pageProps"]["transcriptData"]["translation"]["paragraphs"]
    return "\n\n".join(
        " ".join(cue["text"].replace("\n", " ") for cue in para["cues"]) for para in paragraphs
    )


def collapse(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch(source: Source, session: requests.Session) -> str:
    response = session.get(source.url, timeout=60, headers=HEADERS)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "pdf" in content_type or source.url.lower().endswith(".pdf"):
        return collapse(clean_pdf(response.content))
    if source.extractor == "ted":
        return collapse(clean_ted(response.text))
    return collapse(clean_html(response.text))


def render(source: Source, body: str) -> str:
    """Front-load provenance so a retrieved chunk keeps its attribution."""
    lines = [
        f"# {source.title}",
        "",
        f"**Reading-list entry:** {source.book}",
        f"**Published free by:** {source.publisher}",
        f"**Source:** {source.url}",
        f"**Retrieved:** {date.today().isoformat()}",
        "",
    ]
    if source.note:
        lines += [f"> {source.note}", ""]
    lines += ["---", "", body, ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    written, thin, failed = [], [], []
    for source in SOURCES:
        try:
            body = fetch(source, session)
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            failed.append((source, str(exc)))
            print(f"  FAIL  {source.slug}: {exc}", flush=True)
            continue

        if len(body) < source.min_chars:
            thin.append((source, len(body)))
            print(f"  THIN  {source.slug}: only {len(body)} chars, skipping", flush=True)
            continue

        (args.out / f"{source.slug}.md").write_text(render(source, body), encoding="utf-8")
        written.append((source, len(body)))
        print(f"  ok    {source.slug:<40} {len(body):>7} chars", flush=True)

    # Drop files from earlier runs whose source has since been removed.
    keep = {f"{s.slug}.md" for s in SOURCES}
    for stale in sorted(p for p in args.out.glob("*.md") if p.name not in keep):
        stale.unlink()
        print(f"  pruned {stale.name}", flush=True)

    print(f"\n{len(written)} written, {len(thin)} too thin, {len(failed)} failed -> {args.out}")

    if thin or failed:
        print("\nNeeds attention:", file=sys.stderr)
        for source, size in thin:
            print(f"  thin   {source.slug} ({size} chars) {source.url}", file=sys.stderr)
        for source, exc in failed:
            print(f"  failed {source.slug}: {exc}", file=sys.stderr)

    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
