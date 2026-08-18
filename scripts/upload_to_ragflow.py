"""Upload the formatted material-sample documents into RAGFlow.

Chunk method is a per-dataset setting, so the two output shapes need two
datasets: the flat CSV goes to a ``table``-chunked dataset (one chunk per test
sample), the Markdown to a ``naive``-chunked one.

Re-running is safe: existing datasets are reused and a document whose name
already exists is replaced rather than duplicated.

Reads the API key from the RAGFLOW_API_KEY environment variable so the
credential never lands in the repo.

    RAGFLOW_API_KEY=... python scripts/upload_to_ragflow.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://localhost:9380"
DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "docs" / "rag" / "material-samples"

CSV_DATASET = "SF Materials Knowledge"
DOCS_DATASET = "SF Materials Knowledge - Docs"

DOCS_DESCRIPTION = (
    "Simplifyber foam-formed material samples: slurry formulations, press and coating processes, "
    "and test results, one document per material. Generated from "
    "'Material Samples_Formulations & Testing.xlsx'."
)
CSV_DESCRIPTION = (
    "Individual material test samples as flat rows - GSM, thickness, density, tensile, tear, "
    "flexural and post-soak measurements. One row per specimen."
)

# Parsing is the slow part: embedding runs on CPU here, a few seconds per chunk.
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 1800


class RagflowError(RuntimeError):
    pass


class Ragflow:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _call(self, method: str, path: str, **kwargs) -> dict:
        response = self.session.request(method, f"{self.base}/api/v1{path}", timeout=120, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, None):
            raise RagflowError(f"{method} {path} -> {payload.get('code')}: {payload.get('message')}")
        return payload.get("data")

    def find_dataset(self, name: str) -> dict | None:
        datasets = self._call("GET", "/datasets", params={"page_size": 100}) or []
        return next((d for d in datasets if d["name"] == name), None)

    def ensure_dataset(self, name: str, chunk_method: str, description: str) -> dict:
        existing = self.find_dataset(name)
        if existing:
            print(f"  dataset {name!r} already exists (chunk_method={existing.get('chunk_method')})")
            return existing
        created = self._call(
            "POST", "/datasets",
            json={"name": name, "chunk_method": chunk_method, "description": description},
        )
        print(f"  created dataset {name!r} (chunk_method={chunk_method})")
        return created

    def list_documents(self, dataset_id: str) -> list[dict]:
        data = self._call("GET", f"/datasets/{dataset_id}/documents", params={"page_size": 100})
        return (data or {}).get("docs", [])

    def delete_documents(self, dataset_id: str, ids: list[str]) -> None:
        if ids:
            self._call("DELETE", f"/datasets/{dataset_id}/documents", json={"ids": ids})

    def upload(self, dataset_id: str, paths: list[Path]) -> list[dict]:
        files = [("file", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
        return self._call("POST", f"/datasets/{dataset_id}/documents", files=files) or []

    def parse(self, dataset_id: str, document_ids: list[str]) -> None:
        self._call("POST", f"/datasets/{dataset_id}/chunks", json={"document_ids": document_ids})

    def wait_for_parsing(self, dataset_id: str, label: str) -> list[dict]:
        """Poll until every document leaves the running/pending state."""
        deadline = time.time() + POLL_TIMEOUT_SECONDS
        while True:
            docs = self.list_documents(dataset_id)
            pending = [d for d in docs if d.get("run") in ("RUNNING", "UNSTART", "1", "0")]
            done = len(docs) - len(pending)
            chunks = sum(d.get("chunk_count") or 0 for d in docs)
            print(f"  [{label}] {done}/{len(docs)} parsed, {chunks} chunks so far", flush=True)
            if not pending:
                return docs
            if time.time() > deadline:
                raise RagflowError(f"parsing did not finish within {POLL_TIMEOUT_SECONDS}s for {label}")
            time.sleep(POLL_INTERVAL_SECONDS)


def publish(api: Ragflow, dataset: dict, paths: list[Path], label: str) -> list[dict]:
    dataset_id = dataset["id"]

    # Replace same-named documents so re-runs update rather than duplicate.
    existing = {d["name"]: d["id"] for d in api.list_documents(dataset_id)}
    stale = [existing[p.name] for p in paths if p.name in existing]
    if stale:
        print(f"  replacing {len(stale)} existing document(s)")
        api.delete_documents(dataset_id, stale)

    uploaded = api.upload(dataset_id, paths)
    print(f"  uploaded {len(uploaded)} file(s)")

    api.parse(dataset_id, [d["id"] for d in uploaded])
    print("  parsing started (CPU embedding, expect several minutes)")
    return api.wait_for_parsing(dataset_id, label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--base-url", default=os.environ.get("RAGFLOW_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--only",
        choices=("csv", "markdown"),
        help="Publish just one side. Re-parsing costs minutes of CPU embedding per document, "
        "so skip the half that is already current.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("RAGFLOW_API_KEY")
    if not api_key:
        print("RAGFLOW_API_KEY is not set", file=sys.stderr)
        return 1

    markdown = sorted(args.source.glob("*.md"))
    csv_files = sorted(args.source.glob("*.csv"))
    if not markdown or not csv_files:
        print(f"nothing to upload from {args.source}", file=sys.stderr)
        return 1

    api = Ragflow(args.base_url, api_key)
    published: list[tuple[str, list[dict]]] = []

    if args.only != "markdown":
        print(f"Table dataset -> {CSV_DATASET}")
        csv_dataset = api.ensure_dataset(CSV_DATASET, "table", CSV_DESCRIPTION)
        published.append((CSV_DATASET, publish(api, csv_dataset, csv_files, "csv")))

    if args.only != "csv":
        print(f"\nMarkdown dataset -> {DOCS_DATASET}")
        docs_dataset = api.ensure_dataset(DOCS_DATASET, "naive", DOCS_DESCRIPTION)
        published.append((DOCS_DATASET, publish(api, docs_dataset, markdown, "markdown")))

    print("\nResult:")
    failed = []
    for dataset_name, docs in published:
        print(f"  {dataset_name}")
        for doc in sorted(docs, key=lambda d: d["name"]):
            status = doc.get("run")
            chunks = doc.get("chunk_count") or 0
            print(f"    {doc['name']:<32} {status:<8} {chunks:>4} chunks")
            if status != "DONE" or chunks == 0:
                failed.append((dataset_name, doc["name"], status, chunks))

    if failed:
        print("\nDocuments that did not parse cleanly:", file=sys.stderr)
        for dataset_name, name, status, chunks in failed:
            print(f"  {dataset_name} / {name}: run={status} chunks={chunks}", file=sys.stderr)
        return 1

    print("\nAll documents parsed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
