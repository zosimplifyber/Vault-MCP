"""
Vault MCP Server
Exposes Autodesk Vault REST API operations as MCP tools via SSE transport.
Configuration is loaded from config.json and credentials are set automatically.
"""

import json
import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from vault_rest_api import VaultRestAPI

logger = logging.getLogger(__name__)


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

    return mcp
