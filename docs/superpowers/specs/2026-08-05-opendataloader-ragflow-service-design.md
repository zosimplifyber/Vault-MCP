# OpenDataLoader PDF Service for RAGFlow — Design

**Date:** 2026-08-05
**Status:** Approved, ready for implementation planning

## Problem

Ingesting PDFs into the local RAGFlow instance is slow. The default PDF parser,
DeepDOC, runs vision models on CPU for every page, and this machine has no
practical GPU for the job (an RTX 500 Ada with 4 GB of VRAM, shared with the
display). The documents queued for ingestion — Vault engineering drawings,
standards and specifications, supplier datasheets, textbooks — are overwhelmingly
born-digital and already carry a text layer. Paying for full visual layout
recognition on a page whose text can simply be read is the bottleneck.

RAGFlow v0.26.4 already ships a client for a faster parser:
`deepdoc/parser/opendataloader_parser.py`, selectable in the PDF-parser dropdown
next to DeepDoc, MinerU and Docling. In local mode OpenDataLoader is a
deterministic Java parser that extracts text, reading order, tables and bounding
boxes at roughly 0.015 s/page with no ML model at all.

What is missing is the server. RAGFlow's client is a **remote client only** — it
POSTs to `{OPENDATALOADER_APISERVER}/file_parse` and expects a JSON parse tree
back. RAGFlow's documentation says OpenDataLoader "runs as a standalone service
container", but RAGFlow ships no compose entry for one, and the
`opendataloader-project` organisation publishes only the Java library and its
Python, Node and LangChain wrappers — no server, no image.

This project builds that missing service.

## Scope

**In scope**

- An HTTP service implementing the contract RAGFlow's built-in client already
  speaks: `GET /health` and `POST /file_parse`
- A fast local tier (Java engine, no ML) for PDFs that have a text layer
- An optional hybrid tier (Docling backend, OCR) for scanned documents
- Automatic per-document routing between the two tiers
- Container images and a compose project that attaches to the running RAGFlow
  stack without modifying the RAGFlow checkout
- Tests, including a contract test run against RAGFlow's own parsing logic

**Out of scope**

- Forking, patching or building the OpenDataLoader Java engine from source. The
  published PyPI package and its bundled artifact are used as-is.
- Modifying RAGFlow. The client already exists; we implement the server it
  expects. The only RAGFlow-side change is one environment variable.
- Replacing DeepDOC globally. OpenDataLoader is selected per dataset; DeepDOC
  remains available.
- A persistent-JVM service. See *Known limitations*.
- Any Vault MCP surface. This service is infrastructure for RAGFlow and is not
  exposed as an MCP tool.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Integration point | RAGFlow's existing OpenDataLoader client | It is already in v0.26.4. Writing a server is far less work than a parser, and upgrades keep working. |
| Engine | Published `opendataloader-pdf` package | Apache-2.0, ships its own Java artifact. Building from source buys nothing. |
| Tiers | Two containers, not one | The heavy Docling/OCR image stays stopped unless scans are being ingested; the fast path never loads an ML model. |
| Routing | Automatic, by text-layer detection | Encodes the rule learned the hard way — check for a text layer before paying for the expensive parser — in the service rather than in per-dataset config someone must remember. |
| Explicit override | RAGFlow's `hybrid` form field always wins | Detection is a default, not a policy. The dataset owner can force either tier. |
| Primary output | `json_doc`, always attempted | RAGFlow collapses `md_text` into a single section for the whole document, destroying chunk boundaries and citations. |
| `json_doc` shape | ODL's native JSON, passed through verbatim | RAGFlow's walker already reads ODL's schema. Reshaping would break the bbox → crop path behind source citations. |
| Empty result | HTTP 502, never an empty success | A document that silently yields zero chunks is not discovered until a retrieval comes back wrong. |
| Compose | Standalone project on RAGFlow's external network | The RAGFlow checkout stays clean and its upgrades cannot conflict with our services. |
| Exposure | `odl-api` on loopback only; `odl-hybrid` internal | Matches the reasoning already recorded in this stack's MCP port comment. |
| Hybrid device | CPU by default | GPU passthrough needs WSL2 plumbing and 4 GB of shared VRAM is marginal. Not worth blocking v1. |
| Location | This repo | User's choice. Noted: this is RAGFlow infrastructure living in a Vault MCP repo. |

## The contract

Dictated by `deepdoc/parser/opendataloader_parser.py` and
`rag/llm/ocr_model.py::OpenDataLoaderOcrModel` in the running container. It is
implemented to, not designed.

```
GET  /health
     → 200 {"status": "ok"}
     Optional: Authorization: Bearer <ODL_API_KEY>

POST /file_parse
     multipart/form-data
       file          (filename, bytes, application/pdf)   required
       hybrid        str      optional
       image_output  str      optional
       sanitize      "true"|"false"  optional
     → 200 {"json_doc": <tree>|null, "md_text": <str>|null}
```

Client-side behaviour worth designing around: RAGFlow retries `/file_parse`
**three times**, uses a **600 s** default timeout, and renders page images itself
on the RAGFlow host for crop tags — so the service never returns images.

`json_doc` must satisfy RAGFlow's `_iter_elements` and `_bbox_from_element`:
elements are dicts carrying `type` plus one of `content` / `text` / `cells`,
with `"bounding box"` as `[left, bottom, right, top]` in PDF points and
`"page number"`. Recognised types are `heading`/`title`/`paragraph`/`text`/
`list`/`list_item`/`caption`, `table` (may carry `html`), `image`/`picture`/
`figure`, and `formula`/`equation`. OpenDataLoader's native JSON output already
matches this; RAGFlow's client was written against it.

## Components

```
opendataloader/
  service/
    app.py       FastAPI — /health, /file_parse, auth, concurrency guard
    router.py    text-layer detection → tier decision
    convert.py   wraps opendataloader_pdf.convert(); temp-dir lifecycle
    config.py    environment-variable settings
  Dockerfile.api                     temurin JRE 17 + python + opendataloader-pdf
  Dockerfile.hybrid                  python only, opendataloader-pdf[hybrid];
                                     runs opendataloader-pdf-hybrid. No JRE — the
                                     backend is Docling/Python; only the client
                                     side invokes Java.
  docker-compose.opendataloader.yml  standalone project, external ragflow network
  README.md                          setup, configuration, troubleshooting
tests/
  test_opendataloader_router.py
  test_opendataloader_service.py
  test_opendataloader_ragflow_contract.py
```

`router` decides, `convert` executes, `app` speaks HTTP. Each is testable
without Docker and without RAGFlow.

## Routing

```
if the request carries an explicit `hybrid` field:
    honour it
else:
    chars_per_page = extractable characters / sampled pages      (pypdf)
    chars_per_page >= ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE
        → local tier
    otherwise
        → hybrid: hybrid="docling-fast",
                  hybrid_url=ODL_HYBRID_URL,
                  hybrid_fallback=True
```

With `ODL_ENABLE_HYBRID=false` the service never routes to the hybrid tier and
returns whatever the local tier produced.

## Data flow

1. RAGFlow's task executor parses a PDF with OpenDataLoader selected.
2. It POSTs the bytes to `http://odl-api:5060/file_parse`.
3. `app` authenticates, acquires a concurrency slot, writes the bytes to a
   per-request temp directory.
4. `router` samples the PDF and picks a tier.
5. `convert` calls `opendataloader_pdf.convert(format="markdown,json", …)` in a
   threadpool — it blocks on a subprocess.
6. The emitted `.json` and `.md` are read back.
7. The response is returned and the temp directory removed in a `finally`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ODL_API_KEY` | *(unset)* | When set, `/health` and `/file_parse` require a matching bearer token |
| `ODL_HYBRID_URL` | `http://odl-hybrid:5002` | Hybrid backend address |
| `ODL_HYBRID_BACKEND` | `docling-fast` | Value passed as `hybrid` |
| `ODL_ENABLE_HYBRID` | `true` | `false` disables hybrid routing entirely |
| `ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `50` | Detection threshold |
| `ODL_TEXT_LAYER_SAMPLE_PAGES` | `5` | Pages sampled when detecting |
| `ODL_MAX_CONCURRENCY` | `4` | Simultaneous conversions; each spawns a JVM |
| `ODL_TIMEOUT` | `540` | Per-conversion timeout, deliberately below RAGFlow's 600 s |

## Deployment

The compose file is a standalone project joining RAGFlow's existing
`docker_ragflow` network as `external`.

| Service | Port | Profile | Notes |
| --- | --- | --- | --- |
| `odl-api` | `127.0.0.1:5060` | default | Always on. Small image, no ML models. |
| `odl-hybrid` | `5002`, network-internal | `hybrid` | Opt-in. Named volume caches Docling/OCR models across restarts. Runs `--host 0.0.0.0 --force-ocr --device cpu`. |

A commented GPU block sits alongside the `--device cpu` setting for later.

RAGFlow-side setup is two steps: add
`OPENDATALOADER_APISERVER=http://odl-api:5060` to `ragflow/docker/.env` (the
ragflow service loads it via `env_file: .env`), restart the container, then
select **OpenDataLoader** in the dataset's PDF-parser dropdown. Configuring the
same value from the UI's Model providers page is an equivalent alternative that
edits no files; the README documents both.

## Failure behaviour

| Condition | Response |
| --- | --- |
| `convert()` raises or exits non-zero | `502` — RAGFlow retries three times |
| Hybrid backend unreachable | `hybrid_fallback=True` degrades to local extraction; warning logged |
| JSON missing, markdown present | Return `md_text`, log the single-section degradation loudly |
| Neither produced | `502` — never an empty success |
| PDF is password-protected, or its encryption is unsupported | `400`, before any conversion — no tier can read it, and routing it to OCR would cost ~27 minutes (540 s x 3 RAGFlow retries) to return nothing |
| Conversion exceeds `ODL_TIMEOUT` | `504`, raised before RAGFlow's own timeout fires |
| `ODL_API_KEY` set and bearer absent or wrong | `401` |

## Testing

Written test-first, following this repo's existing pytest conventions.

1. **Router units** — synthetic PDFs built with reportlab, already a dependency
   here: one with a text layer, one image-only. Assert tier selection, threshold
   boundary behaviour, and that an explicit `hybrid` field overrides detection.
2. **Service contract** — FastAPI `TestClient` with `convert` monkeypatched.
   Response shape, `json_doc` preferred over `md_text`, `502` when nothing is
   produced, `401` on bad auth, and passthrough of `hybrid` / `image_output` /
   `sanitize`.
3. **RAGFlow contract** — run a representative `json_doc` through RAGFlow's own
   `_iter_elements`, `_bbox_from_element` and `_transfer_from_json` logic and
   assert non-empty sections and tables with sane bounding boxes. This is where
   the real integration risk sits: output that silently fails to match RAGFlow's
   walker would otherwise only surface as a bad retrieval.
4. **Live smoke** — `/health`, one real Vault drawing end to end, then an actual
   RAGFlow ingest and a retrieval against a real dataset.

## Known limitations

- **A JVM per document.** `opendataloader_pdf.convert()` spawns a JVM per call
  and RAGFlow sends one PDF per request, so roughly 1 s of startup is paid per
  document. Against embedding time this is noise, but it rules the service out
  as a high-throughput per-page API. A persistent-JVM Java service is the
  optimisation if that ever matters.
- **Detection is per document, not per page.** A mostly-digital PDF containing a
  few scanned inserts takes the local path, and those inserts yield nothing.
  Forcing `hybrid` on that dataset is the escape hatch.
- **Hybrid on CPU is slow.** Scanned documents remain expensive under any
  parser. The gain here is that the other four document categories stop paying
  that cost, not that OCR becomes cheap.
- **RAGFlow marks OpenDataLoader experimental.** The client could change shape in
  a future release; the contract test is the early-warning system.

## Verification

Run on 2026-08-06 against the live stack (RAGFlow v0.26.4, `docker-ragflow-cpu-1`).

**Service.** `odl-api` built from `Dockerfile.api` and started via the compose
project on the existing `docker_ragflow` network. Reports `healthy`, which also
confirms the decision to leave `/health` unauthenticated — that status comes
from the very `curl` in `HEALTHCHECK` that would otherwise have failed.
`pypdf[crypto]` pulled `cryptography` 50.0.0 as intended.

**Reachability.** `curl http://odl-api:5060/health` from inside the RAGFlow
container succeeds, and RAGFlow's own client reports
`OpenDataLoader service reachable: True`.

**Parse speed.** A 990 KB, 6-page standards PDF parsed in **2.3 s** including
JVM startup, on the local tier, with no ML model loaded. The router chose
`local` for every born-digital document tested.

**Ingest.** Dataset `odl-verification` created with
`layout_recognize: OpenDataLoader`. `iso4014.pdf` produced 90 sections and 22
chunks. `table_test.pdf` produced 2 chunks in 2.64 s.

**Tables — the finding that changed the design.** RAGFlow's converter, run
against a real captured parse tree, produced **zero tables** and 21 sections
for a 4x4 table: sixteen single-word paragraphs plus four empty `'|  |  |'`
rows. Two one-sided schema mismatches caused it (see *Known limitations*).
After `normalize.py`, the same document indexes as a single chunk containing
the intact table, and a retrieval for "What is the head diameter of an ISO 4014
M10 bolt?" returns it as the top hit with `16.0 mm` still beside `ISO 4014 M10`.
Without this the service would have been a downgrade from DeepDOC on precisely
the table-heavy documents it was built to ingest.

**Bounding boxes.** Every text element in the captured trees carries a usable
`bounding box` and `page number`, so citations link back to the page.

**First-parse gotcha.** The very first document parsed after setting
`OPENDATALOADER_APISERVER` failed with `[ERROR]OpenDataLoader not found.` —
RAGFlow auto-provisions the OCR model entry on first use and a concurrent task
can beat it. Re-parsing succeeded. Documented in the README.

**Not yet verified.** The hybrid/OCR tier: `odl-hybrid` is defined in compose
and has a Dockerfile but has not been built, so no scanned document has been
parsed end to end. Its CLI flags come from upstream documentation rather than
from a binary that has been run.
