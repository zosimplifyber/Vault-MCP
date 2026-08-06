# OpenDataLoader PDF service for RAGFlow

RAGFlow v0.26.4 ships a client for the OpenDataLoader PDF parser
(`/ragflow/deepdoc/parser/opendataloader_parser.py`) but no server to talk to,
and upstream publishes no image. This directory is that server.

Why bother: RAGFlow's default parser, DeepDOC, runs vision models on every page.
On a CPU-only host that is the ingestion bottleneck. OpenDataLoader's local tier
is a deterministic Java parser with no ML model at all, and almost every document
worth ingesting here already has a text layer. Measured on this host, a 990 KB
standards PDF parses in **2.3 s including JVM startup**.

## What runs

| Service | Port | Started by default | Purpose |
| --- | --- | --- | --- |
| `odl-api` | `127.0.0.1:5060` | yes | HTTP contract, routing, fast local tier |
| `odl-hybrid` | internal `5002` | no (`--profile hybrid`) | Docling + OCR for scanned PDFs |

`odl-api` measures each PDF's text layer and sends only documents that lack one
to `odl-hybrid`. If `odl-hybrid` is stopped, `hybrid_fallback` degrades those to
a local parse rather than failing — so the fast tier is useful on its own.

**`odl-hybrid` has not been built yet.** The fast tier covers every document
that already carries a text layer, which is four of the five categories this
was built for. Build the OCR image when scanned paper actually needs ingesting,
and check its `CMD` flags against `opendataloader-pdf-hybrid --help` first.

## Start it

```bash
# fast tier only
docker compose -f opendataloader/docker-compose.opendataloader.yml up -d --build

# with OCR for scanned documents (multi-GB build, slow first start)
docker compose -f opendataloader/docker-compose.opendataloader.yml --profile hybrid up -d --build
```

Check it:

```bash
curl -fsS http://127.0.0.1:5060/health
# {"status":"ok","slots_available":4,"max_concurrency":4}
```

## Point RAGFlow at it

Add to `ragflow/docker/.env` (the ragflow service loads it via `env_file: .env`):

```
OPENDATALOADER_APISERVER=http://odl-api:5060
```

Restart RAGFlow, then in the dataset's **Configuration → Ingestion pipeline**
choose **OpenDataLoader** in the PDF parser dropdown.

The UI's **Model providers** page can configure the same value without editing
a file; the `.env` line is preferred here only because it is reproducible.

Verify from inside the RAGFlow container, which is the network path that
actually matters:

```bash
docker exec docker-ragflow-cpu-1 sh -c 'curl -fsS http://odl-api:5060/health'
docker exec docker-ragflow-cpu-1 sh -c 'cd /ragflow && python -m deepdoc.parser.opendataloader_parser'
# OpenDataLoader service reachable: True
```

## Why this service rewrites tables

RAGFlow's client reads a parse tree shaped by *its own* converter. For text that
happens to match what OpenDataLoader emits — headings, paragraphs, captions and
their bounding boxes pass through untouched. Tables do not, in two ways:

- an OpenDataLoader `table` node carries `rows`, but RAGFlow only visits nodes
  having `content`/`text`/`cells`, so it never sees the table at all;
- a `table row` *does* carry `cells`, so RAGFlow visits it — but the text lives
  in each cell's `kids` while RAGFlow reads `cells[].content`, so the row renders
  as `'|  |  |'`.

Measured against a real 4×4 table, RAGFlow produced **zero tables and sixteen
single-word sections**. "ISO 4014 M10" and "16.0 mm" landed in different chunks
with nothing associating them — so the one question a fastener table exists to
answer could not be answered from the index.

`normalize.py` rewrites each table into the shape RAGFlow understands (an `html`
string plus a flat `cells` list, with the redundant `rows` removed so the same
text is not also scattered as loose sections). Text is untouched, and a test
pins that. Without this the service would be a *downgrade* from DeepDOC on
exactly the table-heavy standards and datasheets it exists to ingest.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ODL_API_KEY` | *(unset)* | When set, `/file_parse` requires `Authorization: Bearer`. Must match `OPENDATALOADER_API_KEY` on the RAGFlow side. `/health` stays open regardless — Docker's HEALTHCHECK sends no header. |
| `ODL_HYBRID_URL` | `http://odl-hybrid:5002` | Hybrid backend address |
| `ODL_HYBRID_BACKEND` | `docling-fast` | Value passed as `hybrid` |
| `ODL_ENABLE_HYBRID` | `true` | `false` disables hybrid routing entirely. Note this is *not* an absolute kill switch: an explicit `hybrid` field from a RAGFlow dataset still wins, by design. |
| `ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `50` | Below this, a document counts as scanned. `0` means "never route to hybrid". |
| `ODL_TEXT_LAYER_SAMPLE_PAGES` | `5` | Pages sampled when detecting |
| `ODL_MAX_CONCURRENCY` | `4` | Simultaneous conversions; each spawns a JVM |
| `ODL_TIMEOUT` | `540` | Per-conversion timeout, below RAGFlow's 600 s |
| `ODL_MAX_UPLOAD_BYTES` | `104857600` | Larger uploads get 413 before being read |

Invalid values fall back to the default **and log a warning**; an unset or empty
variable is silent.

## Troubleshooting

**RAGFlow reports "OpenDataLoader not found" or the parser is unavailable.**
`OPENDATALOADER_APISERVER` is unset or unreachable. Check with the
`docker exec` command above — a working `curl` from the host proves less,
because RAGFlow reaches the service over the compose network.

**Chunks are one enormous block per document.** The service returned `md_text`
without `json_doc`; RAGFlow turns markdown into a single section, losing chunk
boundaries and citations. Check `docker logs odl-api` for the warning naming
the file.

**A document comes back as 400.** It is password-protected, or its encryption
is unsupported. Both are deliberate fast failures: no tier can read it, and
routing it to OCR would cost roughly 27 minutes (540 s × RAGFlow's three
retries) to return nothing. Note the image installs `pypdf[crypto]` for exactly
this reason — without that extra, every AES-encrypted PDF (the default since
Acrobat 7) would be misread as "scanned".

**Requests return 503.** Every conversion slot is busy. A slot is held until its
JVM thread actually finishes, not merely until the request stops waiting, so
sustained 503s mean real work is queued — check `slots_available` on `/health`.

**Scanned PDFs produce nothing.** Either `odl-hybrid` is not running (start it
with `--profile hybrid`) or the document was misdetected. Detection is per
document, so a mostly-digital PDF with a few scanned inserts takes the local
path — force the hybrid tier for that dataset in the RAGFlow UI.

**Ingestion is slower than expected.** Each document costs about a second of JVM
startup, because `convert()` spawns one per call and RAGFlow sends one document
per request. That is inherent to this design.

## Tests

```bash
python -m pytest tests/test_opendataloader_*.py -v
```

`tests/test_opendataloader_ragflow_contract.py` is the important one: it runs
parse trees captured from the real engine through RAGFlow's own element-walking
functions. If it fails after a RAGFlow upgrade, re-copy
`tests/fixtures/ragflow_element_walker.py` from the container before assuming
our service is at fault — the extraction command is in that file's docstring.
