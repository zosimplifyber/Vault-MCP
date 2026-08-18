"""Publish the nonwoven mat cutting research collection into RAGFlow.

A new dataset rather than a merge into the existing 'Ultrasonic Cutting' one: that
dataset is one method deep, at patent and application-note level, for the Fybron /
FyberCom / Fyberite study. This collection is a methods survey across seven cutting
methods, and its central finding is that ultrasonic may be the wrong method for a
non-thermoplastic web. Folding a survey into the study dataset blurs both.

Reuses the RAGFlow client and the replace-rather-than-duplicate publish from
upload_to_ragflow.py.

    RAGFLOW_API_KEY=... python scripts/ingest_nonwoven_cutting.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from upload_to_ragflow import DEFAULT_BASE_URL, Ragflow, RagflowError, publish

SOURCE = Path(__file__).resolve().parent.parent / "docs" / "rag" / "nonwoven-cutting"

DATASET = "Nonwoven Mat Cutting - Methods & Equipment"
DESCRIPTION = (
    "Industrial cutting methods for nonwoven mat and consolidated fibre sheet: rotary, razor, "
    "CNC oscillating knife, die, ultrasonic blade, rotary ultrasonic cut-and-seal, laser and "
    "waterjet. Vendor sources (Sollex, Rinco, Acme Mills, SinapTec, Dukane, TechniWaterjet, "
    "Zund, Eastman) plus a peer-reviewed bibliography. Central finding: the published "
    "literature covers thermoplastic webs, so the single-pass sealed edge from ultrasonic and "
    "laser does not transfer to a cellulosic or foam-formed sheet."
)

# Smoke-test queries, one per major claim the collection is supposed to answer.
SMOKE_TESTS = [
    "Which cutting methods seal the edge and do they work on a non-thermoplastic web?",
    "What is the maximum anvil hardness for ultrasonic blade cutting?",
    "Why is vacuum hold-down the constraint when CNC knife cutting a porous nonwoven?",
    "What is unknown about waterjet cutting a foam-formed cellulosic sheet?",
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

    docs = api.list_documents(dataset["id"])
    if not args.skip_upload:
        print(f"  {len(markdown)} markdown file(s) from {args.source}")
        docs = publish(api, dataset, markdown, "nonwoven-cutting")

    print("\nResult:")
    failed = []
    for doc in sorted(docs, key=lambda d: d["name"]):
        status = doc.get("run")
        chunks = doc.get("chunk_count") or 0
        print(f"  {doc['name']:<46} {status:<8} {chunks:>4} chunks")
        if status != "DONE" or chunks == 0:
            failed.append((doc["name"], status, chunks))

    if failed:
        print("\nDocuments that did not parse cleanly:", file=sys.stderr)
        for name, status, chunks in failed:
            print(f"  {name}: run={status} chunks={chunks}", file=sys.stderr)
        return 1

    try:
        if not smoke_test(api, dataset["id"]):
            print("\nSmoke test returned no chunks for at least one query.", file=sys.stderr)
            return 1
    except RagflowError as exc:
        print(f"\nSmoke test failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nAll {len(docs)} documents parsed and retrievable. Dataset id: {dataset['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
