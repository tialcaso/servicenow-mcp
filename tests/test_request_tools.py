"""Tests for the requested item tools (sc_req_item)."""

import unittest
from unittest.mock import MagicMock, patch

import requests

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.request_tools import (
    GetRequestedItemParams,
    ListRequestedItemsParams,
    OrderCatalogItemParams,
    get_requested_item,
    list_requested_items,
    order_catalog_item,
)
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

SYS_ID = "0123456789abcdef0123456789abcdef"
USER_ID = "fedcba9876543210fedcba9876543210"

RITM = {
    "sys_id": {"value": SYS_ID, "display_value": SYS_ID},
    "number": {"value": "RITM0010001", "display_value": "RITM0010001"},
    "short_description": {"value": "Standard laptop", "display_value": "Standard laptop"},
    "cat_item": {"value": "aaaa0000aaaa0000aaaa0000aaaa0000", "display_value": "Standard laptop"},
    "state": {"value": "1", "display_value": "Open"},
    "stage": {"value": "waiting_for_approval", "display_value": "Waiting for Approval"},
    "opened_at": {"value": "2026-06-01 09:30:00", "display_value": "2026-06-01 02:30:00"},
    "requested_for": {"value": USER_ID, "display_value": "Jane Tester"},
    "request": {"value": "bbbb0000bbbb0000bbbb0000bbbb0000", "display_value": "REQ0010001"},
}


def _ok(result):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"result": result}
    return response


class TestRequestedItemTools(unittest.TestCase):

    def setUp(self):
        auth_config = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username="test", password="test"))
        self.config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=auth_config)
        self.auth_manager = MagicMock(spec=AuthManager)
        self.auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

    @patch("requests.get")
    def test_list_requested_items_for_a_user(self, mock_get):
        mock_get.return_value = _ok([RITM])

        result = list_requested_items(self.config, self.auth_manager, ListRequestedItemsParams(
            requested_for=USER_ID, query="laptop", created_after="2026-06-01", limit=5))

        self.assertTrue(result["success"])
        item = result["requested_items"][0]
        self.assertEqual(item["number"], "RITM0010001")
        self.assertEqual(item["cat_item_display"], "Standard laptop")
        self.assertEqual(item["state"], "1")
        self.assertEqual(item["state_display"], "Open")
        self.assertEqual(item["stage"], "waiting_for_approval")
        self.assertEqual(item["stage_display"], "Waiting for Approval")
        self.assertEqual(item["opened_at"], "2026-06-01 09:30:00")
        self.assertEqual(item["requested_for"], USER_ID)
        self.assertEqual(item["requested_for_display"], "Jane Tester")
        self.assertEqual(item["request"], "REQ0010001")

        args = mock_get.call_args
        self.assertEqual(args[0][0], f"{self.config.api_url}/table/sc_req_item")
        q = args[1]["params"]["sysparm_query"]
        self.assertTrue(q.startswith("short_descriptionLIKElaptop^ORcat_item.nameLIKElaptop^"))
        self.assertIn(f"requested_for={USER_ID}", q)
        self.assertIn("sys_created_on>=2026-06-01 00:00:00", q)
        self.assertTrue(q.endswith("ORDERBYDESCsys_created_on"))
        self.assertEqual(args[1]["params"]["sysparm_limit"], 5)
        self.assertEqual(args[1]["params"]["sysparm_display_value"], "all")

    @patch("requests.get")
    def test_list_requested_items_without_filters(self, mock_get):
        mock_get.return_value = _ok([])
        result = list_requested_items(self.config, self.auth_manager, ListRequestedItemsParams())
        self.assertEqual(result["requested_items"], [])
        self.assertEqual(mock_get.call_args[1]["params"]["sysparm_query"], "ORDERBYDESCsys_created_on")

    @patch("requests.get")
    def test_get_requested_item_by_number(self, mock_get):
        mock_get.return_value = _ok([RITM])
        result = get_requested_item(self.config, self.auth_manager, GetRequestedItemParams(number="RITM0010001"))
        self.assertTrue(result["success"])
        self.assertEqual(result["requested_item"]["sys_id"], SYS_ID)
        self.assertEqual(mock_get.call_args[1]["params"]["sysparm_query"], "number=RITM0010001")

    @patch("requests.get")
    def test_get_requested_item_by_sys_id(self, mock_get):
        mock_get.return_value = _ok([RITM])
        get_requested_item(self.config, self.auth_manager, GetRequestedItemParams(number=SYS_ID))
        self.assertEqual(mock_get.call_args[1]["params"]["sysparm_query"], f"sys_id={SYS_ID}")

    @patch("requests.get")
    def test_get_requested_item_not_found(self, mock_get):
        mock_get.return_value = _ok([])
        result = get_requested_item(self.config, self.auth_manager, GetRequestedItemParams(number="RITM0099999"))
        self.assertFalse(result["success"])
        self.assertIn("not found", result["message"])

    @patch("requests.get")
    def test_get_requested_item_rejects_query_injection(self, mock_get):
        result = get_requested_item(self.config, self.auth_manager,
                                    GetRequestedItemParams(number="RITM1^ORnumberSTARTSWITHRITM"))
        self.assertFalse(result["success"])
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_list_requested_items_surfaces_servicenow_errors(self, mock_get):
        error_response = MagicMock(status_code=403, text="")
        error_response.json.return_value = {"error": {"message": "Insufficient rights"}}
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(response=error_response)
        result = list_requested_items(self.config, self.auth_manager, ListRequestedItemsParams())
        self.assertFalse(result["success"])
        self.assertIn("Insufficient rights", result["message"])


ITEM_ID = "cccc0000cccc0000cccc0000cccc0000"


class TestOrderCatalogItem(unittest.TestCase):

    def setUp(self):
        auth_config = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username="test", password="test"))
        self.config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=auth_config)
        self.auth_manager = MagicMock(spec=AuthManager)
        self.auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

    @patch("requests.get")
    @patch("requests.post")
    def test_orders_and_returns_request_and_requested_item(self, mock_post, mock_get):
        order = MagicMock(status_code=200)
        order.json.return_value = {"result": {"sys_id": "dddd0000dddd0000dddd0000dddd0000",
                                              "number": "REQ0010001", "table": "sc_request"}}
        mock_post.return_value = order
        mock_get.return_value = _ok([{"sys_id": SYS_ID, "number": "RITM0010001"}])

        result = order_catalog_item(self.config, self.auth_manager, OrderCatalogItemParams(
            item_id=ITEM_ID, variables={"start_date": "2026-06-01", "office": "North"},
            requested_for=USER_ID))

        self.assertTrue(result.success)
        self.assertEqual(result.request_number, "REQ0010001")
        self.assertEqual(result.request_sys_id, "dddd0000dddd0000dddd0000dddd0000")
        self.assertEqual(result.ritm_number, "RITM0010001")
        self.assertEqual(result.ritm_sys_id, SYS_ID)
        self.assertEqual(mock_post.call_args[0][0],
                         f"https://dev12345.service-now.com/api/sn_sc/servicecatalog/items/{ITEM_ID}/order_now")
        self.assertEqual(mock_post.call_args[1]["json"], {
            "sysparm_quantity": "1",
            "variables": {"start_date": "2026-06-01", "office": "North"},
            "sysparm_requested_for": USER_ID,
        })
        self.assertEqual(mock_get.call_args[0][0], f"{self.config.api_url}/table/sc_req_item")
        self.assertEqual(mock_get.call_args[1]["params"]["sysparm_query"],
                         "request=dddd0000dddd0000dddd0000dddd0000")

    @patch("requests.post")
    def test_returns_servicenow_refusal_readably(self, mock_post):
        error_response = MagicMock(status_code=400, text="")
        error_response.json.return_value = {"error": {"message": "Mandatory Variables are required"}}
        mock_post.return_value.raise_for_status.side_effect = requests.HTTPError(response=error_response)

        result = order_catalog_item(self.config, self.auth_manager, OrderCatalogItemParams(item_id=ITEM_ID))

        self.assertFalse(result.success)
        self.assertIn("HTTP 400", result.message)
        self.assertIn("Mandatory Variables are required", result.message)

    @patch("requests.get")
    @patch("requests.post")
    def test_order_without_a_visible_requested_item_still_succeeds(self, mock_post, mock_get):
        order = MagicMock(status_code=200)
        order.json.return_value = {"result": {"request_id": "dddd0000dddd0000dddd0000dddd0000",
                                              "request_number": "REQ0010002"}}
        mock_post.return_value = order
        mock_get.return_value = _ok([])

        result = order_catalog_item(self.config, self.auth_manager, OrderCatalogItemParams(item_id=ITEM_ID))

        self.assertTrue(result.success)
        self.assertEqual(result.request_number, "REQ0010002")
        self.assertIsNone(result.ritm_number)
        self.assertNotIn("sysparm_requested_for", mock_post.call_args[1]["json"])

    @patch("requests.post")
    def test_rejects_invalid_sys_ids_without_calling_the_api(self, mock_post):
        for params in (OrderCatalogItemParams(item_id="laptop"),
                       OrderCatalogItemParams(item_id=ITEM_ID, requested_for="jane.tester")):
            self.assertFalse(order_catalog_item(self.config, self.auth_manager, params).success)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
