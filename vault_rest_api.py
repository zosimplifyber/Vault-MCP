"""
Vault REST API Client
Wraps the Autodesk Vault Data API v2 endpoints.
Base URL: {servername}/AutodeskDM/Services/api/vault/v2
"""

import httpx
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

API_PATH = "/AutodeskDM/Services/api/vault/v2"


class VaultRestAPI:
    """
    Client for the Autodesk Vault Data REST API v2.
    All methods return a dict: {"error": bool, "status_code": int, "data": Any}
    """

    def __init__(self, servername: str):
        self.base_url = servername.rstrip("/") + API_PATH
        self._session_token: Optional[str] = None
        self._vault_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def set_session(self, token: str, vault_id: str) -> None:
        """Store the active session token and vault ID after sign-in."""
        self._session_token = token
        self._vault_id = vault_id
        logger.info("Session stored (vault_id=%s)", vault_id)

    def clear_session(self) -> None:
        self._session_token = None
        self._vault_id = None
        logger.info("Session cleared")

    @property
    def is_authenticated(self) -> bool:
        return self._session_token is not None

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._session_token:
            token = self._session_token
            if not token.startswith("Bearer "):
                token = f"Bearer {token}"
            headers["Authorization"] = token
        return headers

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        headers = self._auth_headers()
        if extra_headers:
            headers.update(extra_headers)

        url = self.base_url + endpoint
        log_headers = {
            k: ("Bearer ***" if k == "Authorization" else v)
            for k, v in headers.items()
        }
        logger.info("%s %s  headers=%s  params=%s", method, url, log_headers, params)

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=timeout,
                    follow_redirects=True,
                )
            logger.info("Response %s", resp.status_code)

            try:
                data = resp.json()
            except Exception:
                data = {"content": resp.text}

            if resp.status_code >= 400:
                logger.error("API error %s: %s", resp.status_code, data)
                return {"error": True, "status_code": resp.status_code, "data": data}

            return {"error": False, "status_code": resp.status_code, "data": data}

        except httpx.TimeoutException:
            logger.error("Request timed out: %s %s", method, url)
            return {"error": True, "status_code": 504, "data": {"message": "Request timed out"}}
        except httpx.RequestError as exc:
            logger.error("Connection error: %s", exc)
            return {"error": True, "status_code": 503, "data": {"message": str(exc)}}

    # ------------------------------------------------------------------
    # Informational
    # ------------------------------------------------------------------

    async def get_server_info(self) -> Dict[str, Any]:
        """GET /server-info — product version and metadata."""
        return await self._request("GET", "/server-info")

    async def get_api_spec(self) -> Dict[str, Any]:
        """GET /openapi-spec.yml — OpenAPI specification."""
        return await self._request("GET", "/openapi-spec.yml")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def create_session(
        self,
        database: str,
        username: str,
        password: str,
        app_code: str = "",
    ) -> Dict[str, Any]:
        """
        POST /sessions — sign in and obtain a Bearer token.
        On success the method automatically calls set_session().
        """
        result = await self._request(
            "POST",
            "/sessions",
            json_data={
                "input": {
                    "vault": database,
                    "userName": username,
                    "password": password,
                    "appCode": app_code,
                }
            },
        )
        if not result["error"]:
            data = result["data"]
            # accessToken starts with "V:" and is used as the Bearer token
            access_token = data.get("accessToken", "")
            # vault ID is nested inside vaultInformation
            vault_id = str(
                (data.get("vaultInformation") or {}).get("id", "")
                or data.get("vaultId", "")
                or ""
            )
            if access_token:
                self.set_session(access_token, vault_id)
        return result

    async def delete_session(self, session_id: str) -> Dict[str, Any]:
        """DELETE /sessions/{sessionId} — sign out."""
        result = await self._request("DELETE", f"/sessions/{session_id}")
        self.clear_session()
        return result

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    async def get_groups(
        self,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /groups — list all groups."""
        params: Dict[str, Any] = {"limit": limit}
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request("GET", "/groups", params=params)

    async def get_group_by_id(self, group_id: str) -> Dict[str, Any]:
        """GET /groups/{id} — get a specific group."""
        return await self._request("GET", f"/groups/{group_id}")

    async def get_users(
        self,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /users — list all users."""
        params: Dict[str, Any] = {"limit": limit}
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request("GET", "/users", params=params)

    async def get_user_by_id(self, user_id: str) -> Dict[str, Any]:
        """GET /users/{id} — get a specific user."""
        return await self._request("GET", f"/users/{user_id}")

    async def get_profile_attribute_definitions(
        self,
        filter_association: Optional[str] = None,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /profile-attribute-definitions."""
        params: Dict[str, Any] = {"limit": limit}
        if filter_association:
            params["filter[association]"] = filter_association
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request("GET", "/profile-attribute-definitions", params=params)

    # ------------------------------------------------------------------
    # Vaults
    # ------------------------------------------------------------------

    async def get_vaults(self) -> Dict[str, Any]:
        """GET /vaults — list all vaults accessible by the current session."""
        return await self._request("GET", "/vaults")

    async def get_vault_by_id(self, vault_id: str) -> Dict[str, Any]:
        """GET /vaults/{id} — get a specific vault."""
        return await self._request("GET", f"/vaults/{vault_id}")

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    async def get_folder_contents(
        self,
        vault_id: str,
        folder_id: str = "$",
        *,
        query: Optional[str] = None,
        search_content: bool = False,
        search_sub_folders: bool = False,
        extended_models: bool = False,
        prop_def_ids: Optional[str] = None,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/folders/{id}/contents."""
        resolved = vault_id or self._vault_id or ""
        fid = "root" if folder_id in ("root", "$", "") else folder_id
        params: Dict[str, Any] = {
            "limit": limit,
            "option.searchContent": str(search_content).lower(),
            "option.searchSubFolders": str(search_sub_folders).lower(),
            "extendedModels": str(extended_models).lower(),
        }
        if query:
            params["q"] = query
        if prop_def_ids:
            params["propDefIds"] = prop_def_ids
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request(
            "GET",
            f"/vaults/{resolved}/folders/{fid}/contents",
            params=params,
        )

    async def get_folder_by_id(self, vault_id: str, folder_id: str) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/folders/{id}."""
        resolved = vault_id or self._vault_id or ""
        fid = "root" if folder_id in ("root", "$", "") else folder_id
        return await self._request("GET", f"/vaults/{resolved}/folders/{fid}")

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def get_file_by_id(
        self,
        vault_id: str,
        file_id: str,
        *,
        released_only: bool = False,
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/files/{id}."""
        resolved = vault_id or self._vault_id or ""
        params = {"option.releasedOnly": str(released_only).lower()}
        return await self._request(
            "GET", f"/vaults/{resolved}/files/{file_id}", params=params
        )

    async def get_file_versions(
        self,
        file_id: str,
        *,
        include_properties: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """GET /files/{fileId}/versions."""
        params: Dict[str, Any] = {
            "includeProperties": str(include_properties).lower(),
            "limit": limit,
            "offset": offset,
        }
        return await self._request("GET", f"/files/{file_id}/versions", params=params)

    async def get_file_download_url(self, file_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        """GET /files/{fileId}/download — get download URL for a file."""
        params: Dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        return await self._request("GET", f"/files/{file_id}/download", params=params)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_files(
        self,
        vault_id: str,
        query: str,
        *,
        search_content: bool = False,
        search_sub_folders: bool = True,
        released_files_only: bool = False,
        released_items_only: bool = False,
        latest_only: bool = True,
        extended_models: bool = False,
        prop_def_ids: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/search-results — basic keyword search."""
        resolved = vault_id or self._vault_id or ""
        params: Dict[str, Any] = {
            "q": query,
            "limit": limit,
            "option.searchContent": str(search_content).lower(),
            "option.searchSubFolders": str(search_sub_folders).lower(),
            "option.releasedFilesOnly": str(released_files_only).lower(),
            "option.releasedItemsOnly": str(released_items_only).lower(),
            "option.latestOnly": str(latest_only).lower(),
            "extendedModels": str(extended_models).lower(),
        }
        if prop_def_ids:
            params["propDefIds"] = prop_def_ids
        if sort:
            params["sort"] = sort
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request(
            "GET", f"/vaults/{resolved}/search-results", params=params
        )

    async def advanced_search(
        self,
        vault_id: str,
        search_criteria: Dict[str, Any],
        *,
        extended_models: bool = False,
        prop_def_ids: Optional[str] = None,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /vaults/{vaultId}:advanced-search — structured criteria search."""
        resolved = vault_id or self._vault_id or ""
        body: Dict[str, Any] = {
            "searchCriteria": search_criteria,
            "limit": limit,
        }
        if extended_models:
            body["extendedModels"] = True
        if prop_def_ids:
            body["propDefIds"] = prop_def_ids
        if cursor_state:
            body["cursorState"] = cursor_state
        return await self._request(
            "POST", f"/vaults/{resolved}:advanced-search", json_data=body
        )

    # ------------------------------------------------------------------
    # Property definitions
    # ------------------------------------------------------------------

    async def get_property_definitions(
        self,
        vault_id: str,
        limit: int = 200,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/property-definitions."""
        resolved = vault_id or self._vault_id or ""
        params: Dict[str, Any] = {"limit": limit}
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request(
            "GET", f"/vaults/{resolved}/property-definitions", params=params
        )

    async def get_property_definition_by_id(
        self, vault_id: str, prop_def_id: str
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/property-definitions/{id}."""
        resolved = vault_id or self._vault_id or ""
        return await self._request(
            "GET", f"/vaults/{resolved}/property-definitions/{prop_def_id}"
        )

    # ------------------------------------------------------------------
    # Items (BOM / Engineering items)
    # ------------------------------------------------------------------

    async def get_item_by_id(self, vault_id: str, item_id: str) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/items/{id}."""
        resolved = vault_id or self._vault_id or ""
        return await self._request("GET", f"/vaults/{resolved}/items/{item_id}")

    async def search_items(
        self,
        vault_id: str,
        query: str,
        limit: int = 100,
        cursor_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/items — search for engineering items."""
        resolved = vault_id or self._vault_id or ""
        params: Dict[str, Any] = {"q": query, "limit": limit}
        if cursor_state:
            params["cursorState"] = cursor_state
        return await self._request("GET", f"/vaults/{resolved}/items", params=params)

    # ------------------------------------------------------------------
    # Change orders / Lifecycle
    # ------------------------------------------------------------------

    async def get_lifecycle_definitions(
        self, vault_id: str, limit: int = 100
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/lifecycle-definitions."""
        resolved = vault_id or self._vault_id or ""
        return await self._request(
            "GET", f"/vaults/{resolved}/lifecycle-definitions", params={"limit": limit}
        )

    async def get_category_definitions(
        self, vault_id: str, limit: int = 100
    ) -> Dict[str, Any]:
        """GET /vaults/{vaultId}/category-definitions."""
        resolved = vault_id or self._vault_id or ""
        return await self._request(
            "GET",
            f"/vaults/{resolved}/category-definitions",
            params={"limit": limit},
        )
