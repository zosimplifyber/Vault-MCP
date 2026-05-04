"""
Vault MCP Server
Exposes Autodesk Vault REST API operations as MCP tools via SSE transport.
Configuration is loaded from config.json and credentials are set automatically.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from pdf_watermark import apply_watermark
from vault_rest_api import VaultRestAPI

logger = logging.getLogger(__name__)


def _extract_collection(data: Any) -> List[Dict[str, Any]]:
    """Pull a list of records out of a Vault REST response payload.

    Vault v2 wraps collections in a few different keys depending on the endpoint
    (`results`, `items`, `data`, `value`); this normalizes them.
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "itemVersions", "data", "value", "records"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if data.get("id") or data.get("masterId"):
            return [data]
    return []


def _extract_id(record: Optional[Dict[str, Any]]) -> str:
    """Pull the canonical ID from an item / item-version / file / file-version record."""
    if not isinstance(record, dict):
        return ""
    for key in (
        "id",
        "itemVersionId",
        "fileVersionId",
        "masterId",
        "itemId",
        "fileId",
    ):
        v = record.get(key)
        if v:
            return str(v)
    return ""


def _pick_latest_version(item: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """If the item record embeds a latest-version reference, return (id, record)."""
    if not isinstance(item, dict):
        return "", None
    for key in ("latestItemVersion", "latestVersion", "latest"):
        v = item.get(key)
        if isinstance(v, dict):
            vid = _extract_id(v)
            if vid:
                return vid, v
    for key in ("latestItemVersionId", "latestVersionId"):
        v = item.get(key)
        if v:
            return str(v), None
    return "", None


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str, used: set) -> str:
    """Sanitize a Vault file name for the local filesystem and de-duplicate
    against names already written in this run."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .") or "file.pdf"
    if cleaned not in used:
        return cleaned
    stem = Path(cleaned).stem
    suffix = Path(cleaned).suffix or ".pdf"
    i = 2
    while True:
        candidate = f"{stem} ({i}){suffix}"
        if candidate not in used:
            return candidate
        i += 1


def _latest_by_revision(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the most recent version from a list — prefer revision number, fall back to ID."""
    if not versions:
        return None

    def sort_key(v: Dict[str, Any]):
        for k in ("revisionNumber", "revNumber", "versionNumber", "version"):
            n = v.get(k)
            if isinstance(n, (int, float)):
                return (1, n)
            if isinstance(n, str) and n.isdigit():
                return (1, int(n))
        rid = _extract_id(v)
        try:
            return (0, int(rid)) if rid else (0, 0)
        except ValueError:
            return (0, 0)

    return sorted(versions, key=sort_key)[-1]


def create_mcp_server(api: VaultRestAPI, vault_id: str) -> FastMCP:
    """
    Build and return a FastMCP instance wired to the provided VaultRestAPI client.
    vault_id is the resolved vault ID obtained after sign-in (used as the default
    vault for all vault-scoped calls).
    """

    mcp = FastMCP(
        name="vault-mcp",
        instructions=(
            "MCP server for Autodesk Vault. "
            "Authentication is handled automatically from config.json. "
            "Use the tools below to browse vaults, search files, read properties, and more."
        ),
    )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _resolved_vault(v: Optional[str]) -> str:
        """Return the caller-supplied vault ID, falling back to the config vault."""
        return (v or vault_id or "").strip()

    def _fmt(result: Dict[str, Any]) -> str:
        """Serialize an API result dict to a pretty JSON string."""
        return json.dumps(result, indent=2, default=str)

    # ------------------------------------------------------------------
    # Server information
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_get_server_info() -> str:
        """
        Get Vault server information including product version and metadata.
        No parameters required.
        """
        result = await api.get_server_info()
        return _fmt(result)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_sign_in(username: str, password: str, database: str) -> str:
        """
        Authenticate with the Vault server and obtain a session token.
        Use this tool only when you need to sign in with different credentials
        than those in config.json. The server auto-signs-in on startup.

        Args:
            username: Vault username.
            password: Vault password.
            database: Vault database name (e.g. "Vault").
        """
        result = await api.create_session(database, username, password)
        if result["error"]:
            return _fmt(result)
        # Update vault_id from vaultInformation.id so subsequent tool calls use it
        nonlocal vault_id
        new_id = str(
            (result["data"].get("vaultInformation") or {}).get("id", "")
            or api._vault_id
            or vault_id
        )
        vault_id = new_id
        return _fmt(result)

    @mcp.tool()
    async def vault_sign_out(session_id: str) -> str:
        """
        Sign out and invalidate the current session.

        Args:
            session_id: The session / ticket ID to invalidate.
        """
        result = await api.delete_session(session_id)
        return _fmt(result)

    # ------------------------------------------------------------------
    # Vaults
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_list_vaults() -> str:
        """
        List all vaults accessible with the current session.
        Returns vault names, IDs, and descriptions.
        """
        result = await api.get_vaults()
        return _fmt(result)

    @mcp.tool()
    async def vault_get_vault(vault_id_param: str = "") -> str:
        """
        Get details for a specific vault.

        Args:
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_vault_by_id(_resolved_vault(vault_id_param))
        return _fmt(result)

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_get_folder_contents(
        folder_id: str = "$",
        vault_id_param: str = "",
        query: str = "",
        search_sub_folders: bool = False,
        limit: int = 100,
    ) -> str:
        """
        List the contents of a vault folder (files and sub-folders).

        Args:
            folder_id: Folder ID. Use "$" or leave empty for the root folder.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            query: Optional keyword filter applied to folder contents.
            search_sub_folders: When True, include results from sub-folders.
            limit: Maximum number of results to return (default 100).
        """
        result = await api.get_folder_contents(
            vault_id=_resolved_vault(vault_id_param),
            folder_id=folder_id or "$",
            query=query or None,
            search_sub_folders=search_sub_folders,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_folder(folder_id: str, vault_id_param: str = "") -> str:
        """
        Get metadata for a specific folder.

        Args:
            folder_id: The folder ID to retrieve.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_folder_by_id(_resolved_vault(vault_id_param), folder_id)
        return _fmt(result)

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_get_file(
        file_id: str,
        vault_id_param: str = "",
        released_only: bool = False,
    ) -> str:
        """
        Get metadata for a specific file.

        Args:
            file_id: The file ID to retrieve.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            released_only: When True, only return the file if it is in a released state.
        """
        result = await api.get_file_by_id(
            vault_id=_resolved_vault(vault_id_param),
            file_id=file_id,
            released_only=released_only,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_file_versions(
        file_id: str,
        include_properties: bool = False,
        limit: int = 50,
    ) -> str:
        """
        Get all versions of a file.

        Args:
            file_id: The master file ID.
            include_properties: When True, include user-defined properties in the response.
            limit: Maximum number of versions to return.
        """
        result = await api.get_file_versions(
            file_id=file_id,
            include_properties=include_properties,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_file_download_url(file_id: str, version: int = 0) -> str:
        """
        Get the download URL for a file.

        Args:
            file_id: The file ID.
            version: Specific version number. Use 0 for the latest version.
        """
        result = await api.get_file_download_url(
            file_id=file_id,
            version=version if version > 0 else None,
        )
        return _fmt(result)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_search_files(
        query: str,
        vault_id_param: str = "",
        search_content: bool = False,
        search_sub_folders: bool = True,
        released_files_only: bool = False,
        latest_only: bool = True,
        limit: int = 50,
    ) -> str:
        """
        Search for files in the vault using a keyword query.

        Args:
            query: Keyword(s) to search for (e.g. "pump assembly").
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            search_content: When True, also search inside file content.
            search_sub_folders: When True, include sub-folders in the search.
            released_files_only: When True, only return released files.
            latest_only: When True, return only the latest version of each file.
            limit: Maximum number of results to return.
        """
        result = await api.search_files(
            vault_id=_resolved_vault(vault_id_param),
            query=query,
            search_content=search_content,
            search_sub_folders=search_sub_folders,
            released_files_only=released_files_only,
            latest_only=latest_only,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_advanced_search(
        search_criteria_json: str,
        vault_id_param: str = "",
        limit: int = 50,
    ) -> str:
        """
        Perform an advanced search using structured criteria.

        Args:
            search_criteria_json: JSON string describing the search criteria.
                Example: {"conditions": [{"propDefId": "35", "value": "Released"}]}
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of results to return.
        """
        try:
            criteria = json.loads(search_criteria_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": True, "message": f"Invalid JSON in search_criteria_json: {exc}"})

        result = await api.advanced_search(
            vault_id=_resolved_vault(vault_id_param),
            search_criteria=criteria,
            limit=limit,
        )
        return _fmt(result)

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_list_groups(limit: int = 100) -> str:
        """
        List all groups defined in the vault.

        Args:
            limit: Maximum number of groups to return.
        """
        result = await api.get_groups(limit=limit)
        return _fmt(result)

    @mcp.tool()
    async def vault_get_group(group_id: str) -> str:
        """
        Get details for a specific group.

        Args:
            group_id: The group ID.
        """
        result = await api.get_group_by_id(group_id)
        return _fmt(result)

    @mcp.tool()
    async def vault_list_users(limit: int = 100) -> str:
        """
        List all users in the vault.

        Args:
            limit: Maximum number of users to return.
        """
        result = await api.get_users(limit=limit)
        return _fmt(result)

    @mcp.tool()
    async def vault_get_user(user_id: str) -> str:
        """
        Get details for a specific user.

        Args:
            user_id: The user ID.
        """
        result = await api.get_user_by_id(user_id)
        return _fmt(result)

    # ------------------------------------------------------------------
    # Property definitions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_list_property_definitions(
        vault_id_param: str = "", limit: int = 200
    ) -> str:
        """
        List all user-defined property definitions in the vault.

        Args:
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of definitions to return.
        """
        result = await api.get_property_definitions(
            vault_id=_resolved_vault(vault_id_param), limit=limit
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_property_definition(
        prop_def_id: str, vault_id_param: str = ""
    ) -> str:
        """
        Get a specific property definition by its ID.

        Args:
            prop_def_id: The property definition ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_property_definition_by_id(
            vault_id=_resolved_vault(vault_id_param), prop_def_id=prop_def_id
        )
        return _fmt(result)

    # ------------------------------------------------------------------
    # Items (Engineering)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_search_items(
        query: str,
        vault_id_param: str = "",
        limit: int = 50,
    ) -> str:
        """
        Search for engineering items (BOM items) in the vault.

        Args:
            query: Keyword(s) to search for.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of results to return.
        """
        result = await api.search_items(
            vault_id=_resolved_vault(vault_id_param), query=query, limit=limit
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item(item_id: str, vault_id_param: str = "") -> str:
        """
        Get details for a specific engineering item.

        Args:
            item_id: The item ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_item_by_id(
            vault_id=_resolved_vault(vault_id_param), item_id=item_id
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_version_history(
        item_id: str, vault_id_param: str = "", limit: int = 50
    ) -> str:
        """
        Get the full version history for a master engineering item.

        Args:
            item_id: The master item ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of versions to return.
        """
        result = await api.get_item_version_history(
            vault_id=_resolved_vault(vault_id_param), item_id=item_id, limit=limit
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_change_orders(
        item_id: str, vault_id_param: str = "", limit: int = 100
    ) -> str:
        """
        Get change orders linked to a specific item.

        Args:
            item_id: The master item ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of change orders to return.
        """
        result = await api.get_item_change_orders(
            vault_id=_resolved_vault(vault_id_param), item_id=item_id, limit=limit
        )
        return _fmt(result)

    # ------------------------------------------------------------------
    # Item versions & Bill of Materials
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_list_item_versions(
        query: str = "", vault_id_param: str = "", limit: int = 100
    ) -> str:
        """
        List item versions in the vault, optionally filtered by a keyword query.

        Args:
            query: Optional keyword filter (e.g. a part number). Leave empty to list all.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of results to return.
        """
        result = await api.list_item_versions(
            vault_id=_resolved_vault(vault_id_param),
            query=query or None,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_version(
        item_version_id: str, vault_id_param: str = ""
    ) -> str:
        """
        Get details for a specific item version (a versioned engineering item).

        Args:
            item_version_id: The item-version ID (different from the master item ID).
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_item_version_by_id(
            vault_id=_resolved_vault(vault_id_param), item_version_id=item_version_id
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_bom(
        item_version_id: str, vault_id_param: str = "", limit: int = 200
    ) -> str:
        """
        Get the Bill of Materials (child items) for a specific item version.

        The BOM endpoint operates on item-versions, not master items. If you only have
        a part number, use vault_get_bom_by_part_number for the full lookup chain.

        Args:
            item_version_id: The item-version ID whose BOM you want.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of BOM rows to return.
        """
        result = await api.get_item_bom(
            vault_id=_resolved_vault(vault_id_param),
            item_version_id=item_version_id,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_parents(
        item_version_id: str, vault_id_param: str = "", limit: int = 100
    ) -> str:
        """
        Get parent items (where-used) for an item version.

        Args:
            item_version_id: The item-version ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of parents to return.
        """
        result = await api.get_item_parents(
            vault_id=_resolved_vault(vault_id_param),
            item_version_id=item_version_id,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_item_associated_files(
        item_version_id: str, vault_id_param: str = "", limit: int = 100
    ) -> str:
        """
        Get files associated with a specific item version (e.g. CAD files linked to the item).

        Args:
            item_version_id: The item-version ID.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of associated files to return.
        """
        result = await api.get_item_associated_files(
            vault_id=_resolved_vault(vault_id_param),
            item_version_id=item_version_id,
            limit=limit,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_cad_bom_by_part_number(
        part_number: str, vault_id_param: str = "", limit: int = 200
    ) -> str:
        """
        Convenience tool: look up a CAD assembly file by part number / file name,
        resolve its latest file version, and return the CAD Bill of Materials
        (child file associations) in a single call.

        The CAD BOM reflects the assembly structure as modeled in CAD (.iam → child
        files), which may differ from the engineering item BOM. Returns one level deep.
        Use vault_get_bom_by_part_number for the engineering item BOM instead.

        Returns a JSON object containing:
          - matched_file: the file that was found
          - file_version_id: the file-version used for the BOM lookup
          - bom: the CAD BOM response (child file associations)
          - notes: any warnings (e.g. multiple matches)

        Args:
            part_number: Exact or partial part number / file name to search for.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of BOM rows to return.
        """
        vault = _resolved_vault(vault_id_param)
        notes: list[str] = []

        search = await api.search_files(
            vault_id=vault,
            query=part_number,
            search_sub_folders=True,
            latest_only=True,
            limit=10,
        )
        if search["error"]:
            return _fmt({"step": "search_files", "result": search})

        files = _extract_collection(search.get("data"))
        if not files:
            return _fmt({
                "step": "search_files",
                "message": f"No files found matching '{part_number}'.",
                "result": search,
            })

        if len(files) > 1:
            notes.append(
                f"{len(files)} files matched '{part_number}'; using the first. "
                "Refine the part number to disambiguate."
            )

        matched = files[0]
        file_version_id, embedded = _pick_latest_version(matched)
        if not file_version_id:
            file_version_id = _extract_id(matched)
        if not file_version_id:
            return _fmt({
                "step": "extract_file_version_id",
                "message": "Could not determine the file-version ID from the search response.",
                "matched_file": matched,
            })

        bom = await api.get_file_uses(
            vault_id=vault, file_version_id=file_version_id, limit=limit
        )

        return _fmt({
            "matched_file": matched,
            "file_version_id": file_version_id,
            "bom": bom,
            "notes": notes,
        })

    @mcp.tool()
    async def vault_get_bom_by_part_number(
        part_number: str, vault_id_param: str = "", limit: int = 200
    ) -> str:
        """
        Convenience tool: look up an item by part number, resolve its latest version,
        and return the Bill of Materials in a single call.

        Returns a JSON object containing:
          - matched_item: the master item that was found
          - item_version: the item-version used for the BOM lookup
          - bom: the BOM response (child items)
          - notes: any warnings (e.g. multiple matches, no version found)

        Args:
            part_number: The exact or partial part number to search for.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            limit: Maximum number of BOM rows to return.
        """
        vault = _resolved_vault(vault_id_param)
        notes: list[str] = []

        search = await api.search_items(vault_id=vault, query=part_number, limit=10)
        if search["error"]:
            return _fmt({"step": "search_items", "result": search})

        items = _extract_collection(search.get("data"))
        if not items:
            return _fmt({
                "step": "search_items",
                "message": f"No items found matching '{part_number}'.",
                "result": search,
            })

        if len(items) > 1:
            notes.append(
                f"{len(items)} items matched '{part_number}'; using the first. "
                "Refine the part number to disambiguate."
            )

        master = items[0]
        item_id = _extract_id(master)
        if not item_id:
            return _fmt({
                "step": "extract_item_id",
                "message": "Could not determine the master item ID from the search response.",
                "matched_item": master,
            })

        item_version_id, item_version = _pick_latest_version(master)

        if not item_version_id:
            history = await api.get_item_version_history(
                vault_id=vault, item_id=item_id, limit=50
            )
            if history["error"]:
                return _fmt({
                    "step": "get_item_version_history",
                    "matched_item": master,
                    "result": history,
                })
            versions = _extract_collection(history.get("data"))
            if not versions:
                return _fmt({
                    "step": "get_item_version_history",
                    "message": "Item has no versions.",
                    "matched_item": master,
                })
            item_version = _latest_by_revision(versions)
            item_version_id = _extract_id(item_version)

        if not item_version_id:
            return _fmt({
                "step": "resolve_item_version_id",
                "message": "Could not determine an item-version ID.",
                "matched_item": master,
                "item_version_candidate": item_version,
            })

        bom = await api.get_item_bom(
            vault_id=vault, item_version_id=item_version_id, limit=limit
        )

        return _fmt({
            "matched_item": master,
            "item_version": item_version,
            "item_version_id": item_version_id,
            "bom": bom,
            "notes": notes,
        })

    # ------------------------------------------------------------------
    # Lifecycle & Categories
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_list_lifecycle_definitions(vault_id_param: str = "") -> str:
        """
        List all lifecycle definitions configured in the vault.

        Args:
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_lifecycle_definitions(_resolved_vault(vault_id_param))
        return _fmt(result)

    @mcp.tool()
    async def vault_list_category_definitions(vault_id_param: str = "") -> str:
        """
        List all category definitions configured in the vault.

        Args:
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_category_definitions(_resolved_vault(vault_id_param))
        return _fmt(result)

    # ------------------------------------------------------------------
    # Jobs (Vault Job Queue)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_get_job_queue_enabled(vault_id_param: str = "") -> str:
        """
        Check whether the Vault job queue is enabled.

        If the queue is disabled, jobs submitted via vault_submit_job will sit
        unprocessed until a Job Processor agent comes online and the queue is
        re-enabled. Returns a boolean wrapped in the standard response envelope.

        Args:
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_job_queue_enabled(_resolved_vault(vault_id_param))
        return _fmt(result)

    @mcp.tool()
    async def vault_submit_job(
        job_type: str,
        params_json: str = "{}",
        description: str = "",
        priority: int = 0,
        vault_id_param: str = "",
    ) -> str:
        """
        Submit a job to the Vault job queue. The job will be picked up by the
        next available Job Processor agent that handles its job type.

        The Vault Data API v2 does NOT provide list-all-jobs or cancel-job
        endpoints — capture the returned job ID and poll vault_get_job(id) to
        track status. Status values are: Ready, Running, Success, Failure.

        Job types are environment-specific. Common Autodesk-shipped types include:
          - "Autodesk.Vault.CreateVisualization"  (DWF/PDF generation)
          - "Autodesk.Vault.SyncProperties"       (property sync)
          - "Autodesk.Vault.UpdateRevTable"       (drawing rev-block update)
        Custom Job Processors registered by your team are also valid.

        Args:
            job_type: Job type string. Must match a Job Processor installed in
                your environment.
            params_json: JSON object of string-keyed string-valued parameters,
                e.g. '{"FileMasterId": "12345", "PriorityProcessing": "true"}'.
                Required by most job types — check the Job Processor's docs.
            description: Optional human-readable description of the job.
            priority: Optional priority (1 = highest). 0 means leave unset
                (server uses its default).
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError as exc:
            return json.dumps({
                "error": True,
                "message": f"Invalid JSON in params_json: {exc}",
            })

        if not isinstance(params, dict):
            return json.dumps({
                "error": True,
                "message": "params_json must be a JSON object (dict).",
            })

        result = await api.submit_job(
            vault_id=_resolved_vault(vault_id_param),
            job_type=job_type,
            params={str(k): str(v) for k, v in params.items()},
            description=description or None,
            priority=priority if priority > 0 else None,
        )
        return _fmt(result)

    @mcp.tool()
    async def vault_get_job(job_id: str, vault_id_param: str = "") -> str:
        """
        Get a job's metadata and current status by its ID.

        Use this to poll a job submitted via vault_submit_job. Status values:
        Ready (queued), Running, Success, Failure.

        Args:
            job_id: The job ID returned by vault_submit_job.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
        """
        result = await api.get_job_by_id(
            vault_id=_resolved_vault(vault_id_param), job_id=job_id
        )
        return _fmt(result)

    # ------------------------------------------------------------------
    # PDF watermarking
    # ------------------------------------------------------------------

    @mcp.tool()
    async def vault_watermark_pdfs_in_folder(
        folder_id: str,
        watermark_text: str,
        output_dir: str,
        vault_id_param: str = "",
        font_size: int = 80,
        color: str = "#888888",
        opacity: float = 0.3,
        rotation: float = 45.0,
        limit: int = 200,
    ) -> str:
        """
        Download every PDF in a Vault folder, apply a centered watermark to all
        pages, and save the watermarked copies to a local directory.

        Outputs are saved as ``{output_dir}/{original_name}`` — the original
        Vault files are never modified. The watermark is rendered with reportlab
        and merged with pypdf, so each page gets a same-size overlay.

        Args:
            folder_id: The Vault folder ID to scan. Use vault_get_folder_contents
                first if you don't know the ID.
            watermark_text: The text to stamp on every page (e.g. "DRAFT", a
                customer name, "PRELIMINARY — DO NOT BUILD").
            output_dir: Local directory where the watermarked PDFs will be
                written. Created if it doesn't exist.
            vault_id_param: Vault ID. Leave empty to use the vault from config.json.
            font_size: Watermark font size in points (default 80).
            color: Hex color "#RRGGBB" (default "#888888" gray).
            opacity: Fill opacity 0.0–1.0 (default 0.3, semi-transparent).
            rotation: Rotation in degrees, 0 = horizontal (default 45).
            limit: Max number of folder entries to inspect (default 200).
        """
        vault = _resolved_vault(vault_id_param)
        out_path = Path(output_dir).expanduser().resolve()
        out_path.mkdir(parents=True, exist_ok=True)

        listing = await api.get_folder_contents(
            vault_id=vault, folder_id=folder_id, limit=limit
        )
        if listing["error"]:
            return _fmt({"step": "list_folder", "result": listing})

        entries = _extract_collection(listing.get("data"))
        pdfs = [
            f for f in entries
            if str(f.get("name", "")).lower().endswith(".pdf")
        ]
        if not pdfs:
            return _fmt({
                "step": "filter_pdfs",
                "message": "No .pdf files found in the folder.",
                "folder_id": folder_id,
                "entries_seen": len(entries),
            })

        results: List[Dict[str, Any]] = []
        used_names: set = set()

        for f in pdfs:
            name = str(f.get("name") or "unknown.pdf")
            fv_id, _ = _pick_latest_version(f)
            if not fv_id:
                fv_id = _extract_id(f)
            if not fv_id:
                results.append({
                    "file": name, "ok": False,
                    "error": "could not resolve file-version ID",
                })
                continue

            dl = await api.download_file_version_content(
                vault_id=vault, file_version_id=fv_id
            )
            if dl["error"]:
                results.append({
                    "file": name, "ok": False, "file_version_id": fv_id,
                    "error": f"download failed: {dl['data']}",
                })
                continue

            try:
                watermarked = apply_watermark(
                    dl["data"],
                    watermark_text,
                    font_size=font_size,
                    color=color,
                    opacity=opacity,
                    rotation=rotation,
                )
            except Exception as exc:
                results.append({
                    "file": name, "ok": False, "file_version_id": fv_id,
                    "error": f"watermark failed: {exc}",
                })
                continue

            safe_name = _safe_filename(name, used_names)
            used_names.add(safe_name)
            target = out_path / safe_name
            target.write_bytes(watermarked)
            results.append({
                "file": name, "ok": True, "file_version_id": fv_id,
                "path": str(target), "size_bytes": len(watermarked),
            })

        succeeded = sum(1 for r in results if r["ok"])
        return _fmt({
            "folder_id": folder_id,
            "watermark_text": watermark_text,
            "output_dir": str(out_path),
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "files": results,
        })

    return mcp
