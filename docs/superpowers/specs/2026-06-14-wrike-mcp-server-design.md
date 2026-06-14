# Wrike MCP Server — Design

**Date:** 2026-06-14
**Status:** Approved (design), pending implementation plan
**Project:** Vault-MCP

## Goal

Connect Claude to Wrike via Wrike's REST API v4, implemented as a **second,
independent MCP server inside the existing Vault-MCP program** and surfaced in
the same launcher dashboard. The Wrike server runs on its own port, has its own
Start/Stop panel, and is independent of the Vault session (it starts even if
Vault sign-in fails).

## Context / existing architecture

- `app.py` signs into Vault once, then `create_mcp_server()`
  (`mcp_server.py:125`) builds one `FastMCP` instance (`vault-mcp`) exposing
  ~40 `vault_*` tools over SSE.
- The launcher dashboard (`gui/launcher.py`) has an **MCP SERVER** panel with
  Start/Stop/Open driven by `MCPServerController`, which runs `mcp.sse_app()`
  under uvicorn on a background thread (port 8765 by default).
- Config is `config.json`: a `vault` block plus `server`/`logging`.
- The Vault REST client `vault_rest_api.py` returns `{"error": bool, "data": ...}`
  from every method; the MCP tools serialize results with a local `_fmt()`
  (pretty JSON). The Wrike work mirrors these conventions.

## Decisions (from brainstorming)

- **Form factor:** MCP server (matches the Vault setup).
- **Capabilities:** read tasks/projects, create/update tasks, comments &
  timelogs, contacts/custom-fields/metadata. Write access included.
- **Language/runtime:** Python, official `mcp` SDK (`FastMCP`) + `httpx`.
- **Auth:** Wrike permanent access token (single-user). No OAuth flow.
- **Coexistence:** Separate independent MCP server on a second port, with its
  own panel in the launcher. In Claude, a second MCP endpoint is added.

## Architecture

### New files (in `Vault-MCP/`)

**`wrike_rest_api.py` — `WrikeRestAPI`**
- Async `httpx` client mirroring `vault_rest_api.py`'s style.
- Constructor: `WrikeRestAPI(token, base_url="https://www.wrike.com/api/v4")`.
- Sends `Authorization: Bearer <token>` on every request.
- Single private `_request(method, path, params=None, json=None)` returning
  `{"error": bool, "data": ...}`; non-2xx responses produce an error dict that
  includes the HTTP status and Wrike's `errorDescription`.
- `nextPageToken` pagination handled internally for list endpoints, with a sane
  result cap to avoid unbounded responses.
- One thin method per endpoint used by the tools (below).

**`wrike_mcp_server.py` — `create_wrike_mcp_server(api) -> FastMCP`**
- Builds a `FastMCP(name="wrike-mcp", instructions=...)` instance.
- Defines the `wrike_*` tools, each delegating to `WrikeRestAPI` and returning
  `_fmt(result)` (same pretty-JSON idiom as the Vault server).
- Holds the `readonly` flag; write tools check it first.

### Modified files

**`config.json` / `config.json.example`** — add an optional top-level block:
```json
"wrike": {
  "token": "permanent-access-token-here",
  "base_url": "https://www.wrike.com/api/v4",
  "host": "0.0.0.0",
  "port": 8766,
  "readonly": false
}
```
- `base_url` is configurable because Wrike accounts can live on different data
  centers (US/EU); default is `www.wrike.com`.
- `readonly: true` makes the four write tools refuse.

**`gui/launcher.py`**
- Generalize the existing `MCPServerController` into a reusable controller
  parametrized by a *server-factory callable* (zero-arg, returns a `FastMCP`),
  plus `host`, `port`, and a display `name`. Both Vault and Wrike use it. The
  existing Vault behavior must be preserved exactly.
- Add a **WRIKE MCP SERVER** panel directly under the existing MCP panel,
  mirroring its Start/Stop/Open buttons and endpoint (`url`, `/sse`) display.
- The Wrike controller is constructed from the `wrike` config block (token +
  port), independent of the Vault session. If there is no `wrike` block or no
  token, the panel renders a disabled "Not configured" state instead of erroring.
- When `auto_start_mcp` is set (default `python app.py`), both controllers
  auto-start once the window is up.

**`app.py`**
- `load_config` stays Vault-strict (Vault remains required). The `wrike` block
  is optional and is **not** validated as mandatory.
- SSE/GUI launch path passes config through to the launcher unchanged (the
  launcher builds the Wrike controller from `cfg["wrike"]`).
- Headless SSE mode optionally also starts the Wrike server when a token is
  present (second uvicorn on the Wrike port). If absent, only Vault runs.

## Tools

All Wrike tools are `wrike_`-prefixed, mirroring the `vault_` convention.

**Read**
- `wrike_search_tasks` — list/filter tasks (by title, status, folder, etc.).
- `wrike_get_task` — full detail for one task ID.
- `wrike_list_folders` — folder tree.
- `wrike_get_folder` — one folder's detail.
- `wrike_list_projects` — folders that are projects (project attribute present).
- `wrike_get_subtasks` — subtasks of a given task.

**Write** (refused when `readonly: true`)
- `wrike_create_task` — create a task in a folder (title required; optional
  description, status, importance, dates, responsibles).
- `wrike_update_task` — update status/title/description/dates/responsibles.
- `wrike_move_task` — add/remove parent folders.

**Comments / timelogs**
- `wrike_get_comments` — comments on a task.
- `wrike_create_comment` — post a comment to a task.
- `wrike_get_timelogs` — time entries on a task.
- `wrike_create_timelog` — add a time entry (hours, tracked date, comment).

**Metadata**
- `wrike_list_contacts` — users/contacts.
- `wrike_get_account` — account info.
- `wrike_list_custom_fields` — custom field definitions.
- `wrike_list_workflows` — workflows and their statuses.
- `wrike_list_access_roles` — access roles.

## Endpoint mapping (Wrike v4)

| Tool | Method & path |
|---|---|
| `wrike_search_tasks` | `GET /tasks` (query params) |
| `wrike_get_task` | `GET /tasks/{taskId}` |
| `wrike_list_folders` | `GET /folders` |
| `wrike_get_folder` | `GET /folders/{folderId}` |
| `wrike_list_projects` | `GET /folders` filtered to project folders |
| `wrike_get_subtasks` | `GET /tasks/{ids}` from the task's `subTaskIds` |
| `wrike_create_task` | `POST /folders/{folderId}/tasks` |
| `wrike_update_task` | `PUT /tasks/{taskId}` |
| `wrike_move_task` | `PUT /tasks/{taskId}` with `addParents`/`removeParents` |
| `wrike_get_comments` | `GET /tasks/{taskId}/comments` |
| `wrike_create_comment` | `POST /tasks/{taskId}/comments` |
| `wrike_get_timelogs` | `GET /tasks/{taskId}/timelogs` |
| `wrike_create_timelog` | `POST /tasks/{taskId}/timelogs` |
| `wrike_list_contacts` | `GET /contacts` |
| `wrike_get_account` | `GET /account` |
| `wrike_list_custom_fields` | `GET /customfields` |
| `wrike_list_workflows` | `GET /workflows` |
| `wrike_list_access_roles` | `GET /access_roles` |

## Error handling & safety

- The client maps Wrike error bodies into readable messages: `401`
  invalid token, `403` access forbidden (missing access role), `404` not found,
  `429` rate limited. Tools return these instead of raw HTTP errors, matching
  the Vault tools' behavior.
- Write tools check `readonly` before any API call and return a clear refusal
  message when it is set.
- Two uvicorn servers run in one process, each on its own background thread,
  event loop, and port — Vault on 8765, Wrike on 8766. They are independent;
  stopping or failing one does not affect the other.

## Claude client setup (documentation)

The README gains a short Wrike section: how to mint a permanent token
(Wrike → Apps & Integrations → API), where to put it in `config.json`, and the
second MCP endpoint to register in Claude (`http://localhost:8766/sse`).

## Out of scope (YAGNI)

- OAuth2 app flow / multi-user token refresh.
- Webhooks / real-time push from Wrike.
- Attachments upload/download, dependencies, approvals, and other endpoints not
  in the capability set above.
- A generic "call any Wrike endpoint" passthrough tool.

## Testing

- Unit-test `WrikeRestAPI` against mocked `httpx` responses: success shape,
  pagination across `nextPageToken`, and each error-status mapping.
- Unit-test that write tools refuse when `readonly` is set (no API call made).
- Smoke-test `create_wrike_mcp_server` builds and registers all tools.
- Manual: launch the dashboard, confirm the Wrike panel starts/stops on 8766
  independently of Vault, and that Claude can list tools and read an account.
