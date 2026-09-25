"""
Requested item tools for the ServiceNow MCP server.

This module provides tools for reading service catalog requested items (sc_req_item, "RITM").
"""

import logging
import re
from typing import Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.api import error_detail
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"^[A-Za-z0-9]+$")


class ListRequestedItemsParams(BaseModel):
    """Parameters for listing requested items."""

    requested_for: Optional[str] = Field(
        None, description="Filter by the user the item was requested for (sys_id)"
    )
    limit: int = Field(10, description="Maximum number of requested items to return")
    offset: int = Field(0, description="Offset for pagination")
    query: Optional[str] = Field(
        None, description="Free-text keyword search across short description and catalog item name"
    )
    created_after: Optional[str] = Field(
        None,
        description="Only items created on/after this date ('YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS')",
    )


class GetRequestedItemParams(BaseModel):
    """Parameters for fetching a single requested item."""

    number: str = Field(..., description="Requested item number (e.g. RITM0010001) or sys_id")


def _field(value):
    """``(raw_value, display_value)`` of a Table API field returned with sysparm_display_value=all."""
    if isinstance(value, dict):
        return value.get("value"), value.get("display_value")
    return value, value


def _format_requested_item(item: dict) -> dict:
    """Coded and reference fields as their raw value, with the label alongside as ``*_display``."""
    cat_item, cat_item_display = _field(item.get("cat_item"))
    state, state_display = _field(item.get("state"))
    stage, stage_display = _field(item.get("stage"))
    requested_for, requested_for_display = _field(item.get("requested_for"))
    request_sys_id, request_number = _field(item.get("request"))
    return {
        "sys_id": _field(item.get("sys_id"))[0],
        "number": _field(item.get("number"))[0],
        "short_description": _field(item.get("short_description"))[0],
        "cat_item": cat_item,
        "cat_item_display": cat_item_display,
        "state": state,
        "state_display": state_display,
        "stage": stage,
        "stage_display": stage_display,
        "opened_at": _field(item.get("opened_at"))[0],
        "requested_for": requested_for,
        "requested_for_display": requested_for_display,
        "request": request_number,
        "request_sys_id": request_sys_id,
    }


def _date_bound(value: str) -> str:
    """A date-only value ('YYYY-MM-DD') starts at 00:00:00; a full datetime is used as-is."""
    v = (value or "").strip()
    return f"{v} 00:00:00" if len(v) == 10 else v


def _is_sys_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


_READ_PARAMS = {"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"}


def list_requested_items(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListRequestedItemsParams,
) -> dict:
    """
    List requested items (RITM), newest first.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters for listing requested items.

    Returns:
        Dictionary with the list of requested items.
    """
    # Free-text OR first: ServiceNow groups `^OR` with the immediately preceding term.
    filters = []
    if params.query:
        filters.append(f"short_descriptionLIKE{params.query}^ORcat_item.nameLIKE{params.query}")
    if params.requested_for:
        filters.append(f"requested_for={params.requested_for}")
    if params.created_after:
        filters.append(f"sys_created_on>={_date_bound(params.created_after)}")
    query = "^".join(filters)
    query = f"{query}^ORDERBYDESCsys_created_on" if query else "ORDERBYDESCsys_created_on"

    try:
        response = requests.get(
            f"{config.api_url}/table/sc_req_item",
            params={**_READ_PARAMS, "sysparm_query": query, "sysparm_limit": params.limit,
                    "sysparm_offset": params.offset},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to list requested items: {e}")
        return {
            "success": False,
            "message": f"Failed to list requested items: {error_detail(e)}",
            "requested_items": [],
        }

    items = [_format_requested_item(item) for item in response.json().get("result", [])]
    return {"success": True, "message": f"Found {len(items)} requested items", "requested_items": items}


def get_requested_item(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetRequestedItemParams,
) -> dict:
    """
    Fetch a single requested item by number or sys_id.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Parameters identifying the requested item.

    Returns:
        Dictionary with the requested item details.
    """
    if _is_sys_id(params.number):
        query = f"sys_id={params.number}"
    elif _NUMBER.match(params.number):
        query = f"number={params.number}"
    else:
        return {"success": False, "message": f"Invalid requested item number: {params.number}"}

    try:
        response = requests.get(
            f"{config.api_url}/table/sc_req_item",
            params={**_READ_PARAMS, "sysparm_query": query, "sysparm_limit": 1},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch requested item: {e}")
        return {"success": False, "message": f"Failed to fetch requested item: {error_detail(e)}"}

    result = response.json().get("result", [])
    if not result:
        return {"success": False, "message": f"Requested item not found: {params.number}"}
    item = _format_requested_item(result[0])
    return {"success": True, "message": f"Requested item {item['number']} found", "requested_item": item}
