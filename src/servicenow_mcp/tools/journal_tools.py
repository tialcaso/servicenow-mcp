"""
Journal tools for the ServiceNow MCP server.

This module provides tools for reading the journal (additional comments and work notes) of any
record, stored in sys_journal_field.
"""

import logging
import re
from typing import Literal

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.api import error_detail
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

JournalElement = Literal["comments", "work_notes"]


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
