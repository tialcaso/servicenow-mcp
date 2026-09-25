"""Tests for the journal tools (sys_journal_field)."""

import unittest
from unittest.mock import MagicMock, patch

import requests

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.journal_tools import (
    AddJournalEntryParams,
    ListJournalEntriesParams,
    add_journal_entry,
    list_journal_entries,
)
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

SYS_ID = "0123456789abcdef0123456789abcdef"


class TestListJournalEntries(unittest.TestCase):

    def setUp(self):
        auth_config = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username="test", password="test"))
        self.config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=auth_config)
        self.auth_manager = MagicMock(spec=AuthManager)
        self.auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

    @patch("requests.get")
    def test_lists_comments_newest_first(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": [
            {"value": {"value": "Second update", "display_value": "Second update"},
             "sys_created_on": {"value": "2026-06-02 10:00:00", "display_value": "2026-06-02 03:00:00"},
             "sys_created_by": {"value": "agent.one", "display_value": "agent.one"}},
            {"value": "First update", "sys_created_on": "2026-06-01 10:00:00", "sys_created_by": "agent.two"},
        ]}
        mock_get.return_value = mock_response

        result = list_journal_entries(self.config, self.auth_manager,
                                      ListJournalEntriesParams(table="incident", sys_id=SYS_ID))

        self.assertTrue(result["success"])
        self.assertEqual(result["entries"][0],
                         {"value": "Second update", "created_on": "2026-06-02 10:00:00", "created_by": "agent.one"})
        self.assertEqual(result["entries"][1]["value"], "First update")
        args = mock_get.call_args
        self.assertEqual(args[0][0], f"{self.config.api_url}/table/sys_journal_field")
        self.assertEqual(args[1]["params"]["sysparm_query"],
                         f"name=incident^element_id={SYS_ID}^element=comments^ORDERBYDESCsys_created_on")
        self.assertEqual(args[1]["params"]["sysparm_limit"], 5)
        self.assertEqual(args[1]["params"]["sysparm_display_value"], "all")

    @patch("requests.get")
    def test_work_notes_of_a_requested_item(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_get.return_value = mock_response

        result = list_journal_entries(self.config, self.auth_manager, ListJournalEntriesParams(
            table="sc_req_item", sys_id=SYS_ID, element="work_notes", limit=2))

        self.assertEqual(result["entries"], [])
        q = mock_get.call_args[1]["params"]["sysparm_query"]
        self.assertIn("name=sc_req_item", q)
        self.assertIn("element=work_notes", q)
        self.assertEqual(mock_get.call_args[1]["params"]["sysparm_limit"], 2)

    @patch("requests.get")
    def test_rejects_a_non_sys_id_without_calling_the_api(self, mock_get):
        result = list_journal_entries(self.config, self.auth_manager,
                                      ListJournalEntriesParams(table="incident", sys_id="INC0010001^ORname=x"))
        self.assertFalse(result["success"])
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_rejects_an_invalid_table_name(self, mock_get):
        result = list_journal_entries(self.config, self.auth_manager,
                                      ListJournalEntriesParams(table="incident^ORname=x", sys_id=SYS_ID))
        self.assertFalse(result["success"])
        mock_get.assert_not_called()

    def test_rejects_an_unknown_element(self):
        with self.assertRaises(ValueError):
            ListJournalEntriesParams(table="incident", sys_id=SYS_ID, element="description")

    @patch("requests.get")
    def test_surfaces_servicenow_errors(self, mock_get):
        error_response = MagicMock(status_code=403, text="")
        error_response.json.return_value = {"error": {"message": "Insufficient rights"}}
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(response=error_response)

        result = list_journal_entries(self.config, self.auth_manager,
                                      ListJournalEntriesParams(table="incident", sys_id=SYS_ID))

        self.assertFalse(result["success"])
        self.assertIn("Insufficient rights", result["message"])



class TestAddJournalEntry(unittest.TestCase):

    def setUp(self):
        auth_config = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username="test", password="test"))
        self.config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=auth_config)
        self.auth_manager = MagicMock(spec=AuthManager)
        self.auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

    @patch("requests.patch")
    def test_adds_a_work_note_to_a_requested_item(self, mock_patch):
        mock_patch.return_value.status_code = 200
        mock_patch.return_value.json.return_value = {"result": {"sys_id": SYS_ID, "number": "RITM0010001"}}

        result = add_journal_entry(self.config, self.auth_manager, AddJournalEntryParams(
            table="sc_req_item", sys_id=SYS_ID, text="Called the requester"))

        self.assertTrue(result.success)
        self.assertEqual(result.number, "RITM0010001")
        self.assertEqual(result.message, "Work note added")
        self.assertEqual(mock_patch.call_args[0][0], f"{self.config.api_url}/table/sc_req_item/{SYS_ID}")
        self.assertEqual(mock_patch.call_args[1]["json"], {"work_notes": "Called the requester"})

    @patch("requests.patch")
    def test_adds_a_comment_to_an_incident(self, mock_patch):
        mock_patch.return_value.status_code = 200
        mock_patch.return_value.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}

        result = add_journal_entry(self.config, self.auth_manager, AddJournalEntryParams(
            table="incident", sys_id=SYS_ID, text="We are on it", element="comments"))

        self.assertEqual(result.message, "Comment added")
        self.assertEqual(mock_patch.call_args[1]["json"], {"comments": "We are on it"})

    def test_only_incident_and_requested_item_tables(self):
        with self.assertRaises(ValueError):
            AddJournalEntryParams(table="sys_user", sys_id=SYS_ID, text="x")

    @patch("requests.patch")
    def test_rejects_a_non_sys_id(self, mock_patch):
        result = add_journal_entry(self.config, self.auth_manager,
                                   AddJournalEntryParams(table="incident", sys_id="INC0010001", text="x"))
        self.assertFalse(result.success)
        mock_patch.assert_not_called()

    @patch("requests.patch")
    def test_surfaces_servicenow_errors(self, mock_patch):
        error_response = MagicMock(status_code=403, text="")
        error_response.json.return_value = {"error": {"message": "Insufficient rights"}}
        mock_patch.return_value.raise_for_status.side_effect = requests.HTTPError(response=error_response)

        result = add_journal_entry(self.config, self.auth_manager,
                                   AddJournalEntryParams(table="incident", sys_id=SYS_ID, text="x"))

        self.assertFalse(result.success)
        self.assertIn("Insufficient rights", result.message)

if __name__ == "__main__":
    unittest.main()
