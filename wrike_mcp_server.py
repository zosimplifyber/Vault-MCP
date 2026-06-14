"""
Wrike MCP Server
Exposes Wrike API v4 operations as MCP tools via SSE transport.
Built by create_wrike_mcp_server(); run as a second, independent server
alongside the Vault MCP server (see gui/launcher.py / app.py).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from wrike_rest_api import WrikeRestAPI

logger = logging.getLogger(__name__)


def create_wrike_mcp_server(api: WrikeRestAPI, readonly: bool = False) -> FastMCP:
    """Build a FastMCP instance wired to a WrikeRestAPI client.

    ``readonly`` makes the create/update/move/comment/timelog tools refuse.
    """
    mcp = FastMCP(
        name="wrike-mcp",
        instructions=(
            "MCP server for Wrike (project management). Authentication uses a "
            "permanent access token from config.json. Tools are prefixed "
            "'wrike_'. IDs are Wrike permalink IDs (e.g. tasks 'IEAA…', folders "
            "'IEAF…', contacts 'KUAA…'). Use wrike_list_folders / "
            "wrike_search_tasks to discover IDs before reading or writing."
        ),
    )

    def _fmt(result: Dict[str, Any]) -> str:
        return json.dumps(result, indent=2, default=str)

    def _readonly_refusal(op: str) -> str:
        return _fmt({
            "error": True,
            "data": (f"Refused: '{op}' is a write operation and the Wrike MCP "
                     "server is in read-only mode (set wrike.readonly=false in "
                     "config.json to enable writes)."),
        })

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @mcp.tool()
    async def wrike_get_account() -> str:
        """Get Wrike account information (id, name, date format, subscription)."""
        return _fmt(await api.get_account())

    @mcp.tool()
    async def wrike_list_contacts(me_only: bool = False) -> str:
        """List contacts/users in the account. Set me_only=true for just the
        token owner."""
        return _fmt(await api.list_contacts(me=me_only))

    @mcp.tool()
    async def wrike_search_tasks(
        title: str = "", status: str = "", folder_id: str = "",
        page_size: int = 100,
    ) -> str:
        """Search/list tasks. Optional title substring, status
        (Active/Completed/Deferred/Cancelled), and folder_id to scope to one
        folder/project."""
        return _fmt(await api.search_tasks(
            title=title or None, status=status or None,
            folder_id=folder_id or None, page_size=page_size))

    @mcp.tool()
    async def wrike_get_task(task_id: str) -> str:
        """Get full detail for one task by its Wrike ID."""
        return _fmt(await api.get_task(task_id))

    @mcp.tool()
    async def wrike_list_folders() -> str:
        """List the folder/project tree (ids, titles, parents)."""
        return _fmt(await api.list_folders())

    @mcp.tool()
    async def wrike_get_folder(folder_id: str) -> str:
        """Get one folder/project's detail by ID."""
        return _fmt(await api.get_folder(folder_id))

    @mcp.tool()
    async def wrike_list_projects() -> str:
        """List folders that are projects (have a project owner/status/dates)."""
        return _fmt(await api.list_projects())

    @mcp.tool()
    async def wrike_get_subtasks(task_id: str) -> str:
        """List the subtasks of a task."""
        return _fmt(await api.get_subtasks(task_id))

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @mcp.tool()
    async def wrike_create_task(
        folder_id: str, title: str, description: str = "", status: str = "",
        importance: str = "", start_date: str = "", due_date: str = "",
        responsibles: Optional[List[str]] = None,
        custom_fields: Optional[List[Dict[str, str]]] = None,
        effort_hours: float = 0,
        allow_outside: bool = False,
    ) -> str:
        """Create a task in a folder/project. status: Active/Completed/Deferred/
        Cancelled. importance: High/Normal/Low. Dates ISO 'YYYY-MM-DD'.
        responsibles: list of contact IDs.

        custom_fields: list of {"id": <customFieldId>, "value": <string>} — get IDs
        from wrike_list_custom_fields. Value formats by field type: Contacts =
        comma-separated contact IDs ("KUAA,KUAB"); Multiple = JSON array string
        ('["Option A"]'); DropDown/Text/Numeric = plain string ("Medium-High").
        effort_hours: native task Effort (work) in hours; >0 sets it, 0 leaves it.
        allow_outside: leave false. If the folder is outside the configured safe
        zone the call is blocked; only set true after the USER explicitly confirms
        that out-of-zone creation."""
        if readonly:
            return _readonly_refusal("wrike_create_task")
        blocked = await api.check_folder_access(folder_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.create_task(
            folder_id, title, description=description or None,
            status=status or None, importance=importance or None,
            start_date=start_date or None, due_date=due_date or None,
            responsibles=responsibles or None,
            custom_fields=custom_fields or None,
            effort_hours=effort_hours or None))

    @mcp.tool()
    async def wrike_update_task(
        task_id: str, title: str = "", description: str = "", status: str = "",
        importance: str = "", start_date: str = "", due_date: str = "",
        add_responsibles: Optional[List[str]] = None,
        remove_responsibles: Optional[List[str]] = None,
        custom_fields: Optional[List[Dict[str, str]]] = None,
        effort_hours: float = 0,
        allow_outside: bool = False,
    ) -> str:
        """Update a task's fields. Only non-empty fields are sent.

        custom_fields: list of {"id": <customFieldId>, "value": <string>} (merges —
        only the listed fields change). Get IDs from wrike_list_custom_fields. Value
        formats: Contacts = comma-separated contact IDs; Multiple = JSON array string
        ('["Option A"]'); DropDown/Text/Numeric = plain string.
        effort_hours: native task Effort (work) in hours; >0 sets it, 0 leaves it.
        allow_outside: leave false. If the task is outside the configured safe zone
        the call is blocked; only set true after the USER explicitly confirms that
        specific out-of-zone edit."""
        if readonly:
            return _readonly_refusal("wrike_update_task")
        blocked = await api.check_task_access(task_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.update_task(
            task_id, title=title or None, description=description or None,
            status=status or None, importance=importance or None,
            start_date=start_date or None, due_date=due_date or None,
            add_responsibles=add_responsibles or None,
            remove_responsibles=remove_responsibles or None,
            custom_fields=custom_fields or None,
            effort_hours=effort_hours or None))

    @mcp.tool()
    async def wrike_create_folder(
        parent_id: str, title: str, description: str = "",
        allow_outside: bool = False,
    ) -> str:
        """Create a subfolder / project folder under parent_id.
        allow_outside: leave false unless the USER confirms creating outside the
        safe zone."""
        if readonly:
            return _readonly_refusal("wrike_create_folder")
        blocked = await api.check_folder_access(parent_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.create_folder(parent_id, title,
                                            description=description or None))

    @mcp.tool()
    async def wrike_set_task_fields(
        task_id: str, fields: Dict[str, Any], effort_hours: float = 0,
        allow_outside: bool = False,
    ) -> str:
        """Set task custom fields BY NAME — auto-resolves field IDs and
        validates/normalizes values, so you do NOT need to call
        wrike_list_custom_fields first.

        fields: {field name -> value}, e.g.
          {"Contractor": "Xometry", "Uncertainty Tier": "High", "Owner": "me"}
        - DropDown / Multiple values match the field's allowed options
          (case-insensitive, partial ok: "Xometry" -> "Xometry (Job Shop)").
        - Contacts fields ("Owner") resolve names/emails to contact IDs;
          "me" = token owner. Multiple values: comma-separated string or a list.
        - Other types (Text/Numeric/Currency/Date/Checkbox) pass through.
        effort_hours: native task Effort in hours (>0 sets it).

        If any value can't be resolved, NOTHING is written and the error lists
        valid options. For native title/status/dates/assignee use wrike_update_task.
        allow_outside: leave false unless the USER explicitly confirms an out-of-zone edit."""
        if readonly:
            return _readonly_refusal("wrike_set_task_fields")
        blocked = await api.check_task_access(task_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.set_task_fields_by_name(
            task_id, fields, effort_hours=effort_hours or None))

    @mcp.tool()
    async def wrike_move_task(
        task_id: str, add_parents: Optional[List[str]] = None,
        remove_parents: Optional[List[str]] = None,
        allow_outside: bool = False,
    ) -> str:
        """Move a task between folders by adding/removing parent folder IDs.
        allow_outside: leave false unless the USER confirms moving an out-of-zone task."""
        if readonly:
            return _readonly_refusal("wrike_move_task")
        blocked = await api.check_task_access(task_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.move_task(
            task_id, add_parents=add_parents or None,
            remove_parents=remove_parents or None))

    # ------------------------------------------------------------------
    # Comments / timelogs
    # ------------------------------------------------------------------

    @mcp.tool()
    async def wrike_get_comments(task_id: str) -> str:
        """Get comments on a task."""
        return _fmt(await api.get_comments(task_id))

    @mcp.tool()
    async def wrike_create_comment(task_id: str, text: str,
                                   allow_outside: bool = False) -> str:
        """Post a comment to a task. allow_outside: leave false unless the USER
        confirms commenting on an out-of-zone task."""
        if readonly:
            return _readonly_refusal("wrike_create_comment")
        blocked = await api.check_task_access(task_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.create_comment(task_id, text))

    @mcp.tool()
    async def wrike_get_timelogs(task_id: str) -> str:
        """Get time-tracking entries on a task."""
        return _fmt(await api.get_timelogs(task_id))

    @mcp.tool()
    async def wrike_create_timelog(
        task_id: str, hours: float, tracked_date: str, comment: str = "",
        allow_outside: bool = False,
    ) -> str:
        """Add a time entry to a task. tracked_date ISO 'YYYY-MM-DD'.
        allow_outside: leave false unless the USER confirms an out-of-zone task."""
        if readonly:
            return _readonly_refusal("wrike_create_timelog")
        blocked = await api.check_task_access(task_id, allow_outside)
        if blocked:
            return _fmt(blocked)
        return _fmt(await api.create_timelog(
            task_id, hours, tracked_date, comment=comment or None))

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @mcp.tool()
    async def wrike_list_custom_fields() -> str:
        """List custom field definitions in the account."""
        return _fmt(await api.list_custom_fields())

    @mcp.tool()
    async def wrike_list_workflows() -> str:
        """List workflows and their custom statuses."""
        return _fmt(await api.list_workflows())

    @mcp.tool()
    async def wrike_list_access_roles() -> str:
        """List access roles defined in the account."""
        return _fmt(await api.list_access_roles())

    return mcp
