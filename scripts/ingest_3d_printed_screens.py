"""Publish the 3D-printed screen collection into RAGFlow.

A new dataset: nothing existing covers screens as printed parts. 'Engineering
Reference Data' holds service-bureau spec sheets and machining handbooks,
'Fiber Thermoforming - Patents' covers screen design only as prior art, and
'Simplifyber - Equipment Manuals' is the press, not its tooling. This dataset is
the manufacture of Simplifyber's forming screens by 3D printing: DFM limits,
material choice, post-processing, and what suppliers commit to.

Meeting notes are the first content. Each is ingested twice over: a distilled
note whose sections restate their subject so a naive chunk stands alone, plus the
verbatim transcript for provenance.

Reuses the RAGFlow client and the replace-rather-than-duplicate publish from
upload_to_ragflow.py.

    RAGFLOW_API_KEY=... python scripts/ingest_3d_printed_screens.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from upload_to_ragflow import DEFAULT_BASE_URL, Ragflow, RagflowError, publish

SOURCE = Path(__file__).resolve().parent.parent / "docs" / "rag" / "3d-printed-screens"

DATASET = "3D Printed Screens"
DESCRIPTION = (
    "Manufacture of Simplifyber's forming screens by 3D printing, principally HP Multi Jet "
    "Fusion: wall thickness and warping limits, powder clearing on perforated parts, PA12 "
    "versus PA11 material selection, vapor smoothing, build volumes, lead times, and supplier "
    "meeting notes. Covers what vendors commit to for screens as printed parts, as distinct "
    "from screen design intent or the press they run in."
)

# Smoke-test queries, one per major claim the collection is supposed to answer.
SMOKE_TESTS = [
    "What wall thickness does MJF need for a 3D printed screen, and when is 1 mm acceptable?",
    "Is powder clearing harder or easier on a perforated MJF screen?",
    "Should Simplifyber's screens be printed in PA12 or PA11?",
    "What is the lead time for vapor smoothed MJF screens in small quantities?",
    "What does ABC Corp require to onboard Simplifyber and place the first screen order?",
]


def smoke_test(api: Ragflow, dataset_id: str) -> bool:
    print("\nRetrieval smoke test:")
    ok = True
    for question in SMOKE_TESTS:
        data = api._call(
            "POST", "/retrieval",
            json={"question": question, "dataset_ids": [dataset_id], "top_k": 5, "page_size": 3},
        )
        chunks = (data or {}).get("chunks", [])
        print(f"\n  Q: {question}")
        if not chunks:
            print("    NO CHUNKS RETURNED")
            ok = False
            continue
        for chunk in chunks[:2]:
            snippet = " ".join((chunk.get("content") or "").split())[:160]
            print(f"    [{chunk.get('document_keyword', '?')}] {snippet}...")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--base-url", default=os.environ.get("RAGFLOW_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--skip-upload", action="store_true", help="Only run the smoke test.")
    args = parser.parse_args()

    api_key = os.environ.get("RAGFLOW_API_KEY")
    if not api_key:
        print("RAGFLOW_API_KEY is not set", file=sys.stderr)
        return 1

    markdown = sorted(args.source.glob("*.md"))
    if not markdown:
        print(f"nothing to upload from {args.source}", file=sys.stderr)
        return 1

    api = Ragflow(args.base_url, api_key)

    print(f"Dataset -> {DATASET}")
    dataset = api.ensure_dataset(DATASET, "naive", DESCRIPTION)

    if args.skip_upload:
        docs = api.list_documents(dataset["id"])
    else:
        print(f"  {len(markdown)} document(s) to publish")
        docs = publish(api, dataset, markdown, "screens")

    print("\nResult:")
    failed = []
    for doc in sorted(docs, key=lambda d: d["name"]):
        status = doc.get("run")
        chunks = doc.get("chunk_count") or 0
        print(f"  {doc['name']:<62} {status:<8} {chunks:>4} chunks")
        if status != "DONE" or chunks == 0:
            failed.append((doc["name"], status, chunks))

    if failed:
        print("\nDocuments that did not parse cleanly:", file=sys.stderr)
        for name, status, chunks in failed:
            print(f"  {name}: run={status} chunks={chunks}", file=sys.stderr)
        return 1

    return 0 if smoke_test(api, dataset["id"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
