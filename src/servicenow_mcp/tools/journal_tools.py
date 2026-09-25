"""
Journal tools for the ServiceNow MCP server.

This module provides tools for reading the journal (additional comments and work notes) of any
record, stored in sys_journal_field, and for adding an entry to an incident or requested item.
"""

import logging
import re
from typing import Literal, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.api import error_detail
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

JournalElement = Literal["comments", "work_notes"]
JournalTable = Literal["incident", "sc_req_item"]


class ListJournalEntriesParams(BaseModel):
    """Parameters for listing the journal entries of a record."""

    table: str = Field(..., description="Table of the record, e.g. incident or sc_req_item")
    sys_id: str = Field(..., description="sys_id of the record")
    element: JournalElement = Field(
        "comments",
        description="Journal field: 'comments' (additional comments, visible to the caller) "
        "or 'work_notes' (internal)",
    )
    limit: int = Field(5, description="Maximum number of entries to return (newest first)")


class AddJournalEntryParams(BaseModel):
    """Parameters for adding a journal entry to a record."""

    table: JournalTable = Field(..., description="Table of the record: incident or sc_req_item")
    sys_id: str = Field(..., description="sys_id of the record")
    text: str = Field(..., description="Text of the entry")
    element: JournalElement = Field(
        "work_notes",
        description="Journal field: 'work_notes' (internal) or 'comments' (visible to the caller)",
    )


class JournalEntryResponse(BaseModel):
    """Response from adding a journal entry."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    sys_id: Optional[str] = Field(None, description="sys_id of the updated record")
    number: Optional[str] = Field(None, description="Number of the updated record")


_TABLE_NAME = re.compile(r"^[a-z0-9_]+$")


def _is_sys_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _field(value):
    """Raw value of a Table API field returned with sysparm_display_value=all."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def list_journal_entries(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListJournalEntriesParams,
) -> dict:
    """
    List the journal entries (comments or work notes) of a record, newest first.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters identifying the record and the journal field.

    Returns:
        Dictionary with the entries as ``{value, created_on, created_by}``; ``created_on`` is the
        raw Table API value (UTC).
    """
    if not _TABLE_NAME.match(params.table):
        return {"success": False, "message": f"Invalid table name: {params.table}", "entries": []}
    if not _is_sys_id(params.sys_id):
        return {"success": False, "message": f"Invalid sys_id: {params.sys_id}", "entries": []}

    query_params = {
        "sysparm_query": (
            f"name={params.table}^element_id={params.sys_id}^element={params.element}"
            "^ORDERBYDESCsys_created_on"
        ),
        "sysparm_limit": params.limit,
        "sysparm_fields": "value,sys_created_on,sys_created_by",
        "sysparm_display_value": "all",
    }

    try:
        response = requests.get(
            f"{config.api_url}/table/sys_journal_field",
            params=query_params,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to list journal entries: {e}")
        return {
            "success": False,
            "message": f"Failed to list journal entries: {error_detail(e)}",
            "entries": [],
        }

    entries = [
        {
            "value": _field(item.get("value")),
            "created_on": _field(item.get("sys_created_on")),
            "created_by": _field(item.get("sys_created_by")),
        }
        for item in response.json().get("result", [])
    ]
    return {"success": True, "message": f"Found {len(entries)} journal entries", "entries": entries}


def add_journal_entry(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: AddJournalEntryParams,
) -> JournalEntryResponse:
    """
    Add a work note or an additional comment to an incident or a requested item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters identifying the record, the journal field and the text.

    Returns:
        Response with the updated record's sys_id and number.
    """
    if not _is_sys_id(params.sys_id):
        return JournalEntryResponse(success=False, message=f"Invalid sys_id: {params.sys_id}")

    try:
        response = requests.patch(
            f"{config.api_url}/table/{params.table}/{params.sys_id}",
            json={params.element: params.text},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to add journal entry: {e}")
        return JournalEntryResponse(success=False, message=f"Failed to add journal entry: {error_detail(e)}")

    result = response.json().get("result", {}) or {}
    label = "Work note" if params.element == "work_notes" else "Comment"
    return JournalEntryResponse(
        success=True,
        message=f"{label} added",
        sys_id=result.get("sys_id"),
        number=result.get("number"),
    )
