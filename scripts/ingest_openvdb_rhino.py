"""Publish the OpenVDB-in-Rhino research collection into RAGFlow.

Its own dataset rather than a merge into any existing one: nothing else in the vault
covers CAD tooling, and the retrieval question this collection answers ("why is the
Grasshopper definition slow, and what do I change") shares no vocabulary with the
materials and cutting datasets. Mixing them would only add noise to both.

Reuses the RAGFlow client and the replace-rather-than-duplicate publish from
upload_to_ragflow.py.

    RAGFLOW_API_KEY=... python scripts/ingest_openvdb_rhino.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from upload_to_ragflow import DEFAULT_BASE_URL, Ragflow, RagflowError, publish

SOURCE = Path(__file__).resolve().parent.parent / "docs" / "rag" / "openvdb-rhino"

DATASET = "OpenVDB in Rhino - Dendro, Crystallon, Weaverbird"
DESCRIPTION = (
    "Volumetric modelling in Rhino and Grasshopper via OpenVDB: the VDB data structure and its "
    "tools, the Dendro plug-in component reference, the exact OpenVDB call behind each Dendro "
    "component, Crystallon lattice generation, Weaverbird mesh operations, and Rhino 8's own "
    "bundled OpenVDB behind ShrinkWrap. Built to answer performance questions. Central finding: "
    "cost is set by boundary surface area over voxel size squared times bandwidth - enclosed "
    "volume never enters - and for a gyroid, four of the six stages in the conventional "
    "Crystallon/Weaverbird/Dendro pipeline are not structurally necessary."
)

# Smoke-test queries, one per major claim the collection is supposed to answer.
SMOKE_TESTS = [
    "Why is my Dendro definition slow - what actually drives the active voxel count?",
    "What does Bandwidth do in Dendro Create Settings and what units is it in?",
    "Which Dendro smoothing type is cheapest on a signed distance field, and why?",
    "What is the Crystallon Trim Lattice two-output trap?",
    "Is there a faster route than Crystallon plus Dendro for a gyroid lattice?",
    "Does Rhino 8 ship its own OpenVDB, and does it conflict with Dendro?",
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
        docs = publish(api, dataset, markdown, "openvdb-rhino")

    print("\nResult:")
    failed = []
    for doc in sorted(docs, key=lambda d: d["name"]):
        status = doc.get("run")
        chunks = doc.get("chunk_count") or 0
        print(f"  {doc['name']:<52} {status:<8} {chunks:>4} chunks")
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
