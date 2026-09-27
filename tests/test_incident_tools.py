
import unittest
from unittest.mock import MagicMock, patch

import requests

from servicenow_mcp.tools.incident_tools import (
    CloseIncidentParams,
    CreateIncidentParams,
    DeleteIncidentParams,
    GetIncidentByNumberParams,
    GetIncidentParams,
    ListIncidentsParams,
    ReopenIncidentParams,
    ResolveIncidentParams,
    UpdateIncidentParams,
    close_incident,
    create_incident,
    delete_incident,
    get_incident,
    get_incident_by_number,
    list_incidents,
    reopen_incident,
    resolve_incident,
    update_incident,
)
from servicenow_mcp.utils.config import ServerConfig, AuthConfig, AuthType, BasicAuthConfig
from servicenow_mcp.auth.auth_manager import AuthManager

# A valid-looking 32-char hex sys_id lets us skip the number->sys_id lookup GET.
SYS_ID = "0123456789abcdef0123456789abcdef"


class TestIncidentTools(unittest.TestCase):

    def setUp(self):
        self.auth_config = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username='test', password='test'))
        self.config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=self.auth_config)
        self.auth_manager = MagicMock(spec=AuthManager)
        self.auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

    @patch('requests.get')
    def test_get_incident_by_number_success(self, mock_get):
        # Mock the server configuration
        config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=self.auth_config)

        # Mock the authentication manager
        auth_manager = MagicMock(spec=AuthManager)
        auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

        # Mock the requests.get call
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": [
                {
                    "sys_id": "12345",
                    "number": "INC0010001",
                    "short_description": "Test incident",
                    "description": "This is a test incident",
                    "state": "New",
                    "priority": "1 - Critical",
                    "assigned_to": "John Doe",
                    "category": "Software",
                    "subcategory": "Email",
                    "sys_created_on": "2025-06-25 10:00:00",
                    "sys_updated_on": "2025-06-25 10:00:00"
                }
            ]
        }
        mock_get.return_value = mock_response

        # Call the function with test data
        params = GetIncidentByNumberParams(incident_number="INC0010001")
        result = get_incident_by_number(config, auth_manager, params)

        # Assert the results
        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "Incident INC0010001 found")
        self.assertIn("incident", result)
        self.assertEqual(result["incident"]["number"], "INC0010001")

    @patch('requests.get')
    def test_get_incident_by_number_not_found(self, mock_get):
        # Mock the server configuration
        config = ServerConfig(instance_url="https://dev12345.service-now.com", auth=self.auth_config)

        # Mock the authentication manager
        auth_manager = MagicMock(spec=AuthManager)
        auth_manager.get_headers.return_value = {"Authorization": "Bearer FAKE_TOKEN"}

        # Mock the requests.get call for a not found scenario
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_get.return_value = mock_response

        # Call the function with a non-existent incident number
        params = GetIncidentByNumberParams(incident_number="INC9999999")
        result = get_incident_by_number(config, auth_manager, params)

        # Assert the results
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Incident not found: INC9999999")

    @patch('requests.put')
    def test_resolve_incident_omits_resolved_at(self, mock_put):
        """resolve_incident must NOT send resolved_at='now' (ServiceNow sets it
        automatically; the literal 'now' is invalid and silently dropped)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_put.return_value = mock_response

        result = resolve_incident(self.config, self.auth_manager, ResolveIncidentParams(
            incident_id=SYS_ID, resolution_code="Solution provided", resolution_notes="done"))

        self.assertTrue(result.success)
        sent = mock_put.call_args.kwargs["json"]
        self.assertNotIn("resolved_at", sent)          # the bug that broke resolve
        self.assertEqual(sent["state"], "6")
        self.assertEqual(sent["close_code"], "Solution provided")
        self.assertEqual(sent["close_notes"], "done")

    @patch('requests.put')
    def test_resolve_incident_surfaces_servicenow_error_detail(self, mock_put):
        """A failed resolve must surface ServiceNow's error detail, not just '403'."""
        err_resp = MagicMock()
        err_resp.status_code = 403
        err_resp.json.return_value = {
            "error": {"message": "Operation Failed",
                      "detail": "Data Policy Exception: The following fields are mandatory: Resolution code"},
            "status": "failure",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=err_resp)
        mock_put.return_value = mock_response

        result = resolve_incident(self.config, self.auth_manager, ResolveIncidentParams(
            incident_id=SYS_ID, resolution_code="bad", resolution_notes="x"))

        self.assertFalse(result.success)
        self.assertIn("Resolution code", result.message)
        self.assertIn("403", result.message)

    @patch('requests.delete')
    def test_delete_incident_success(self, mock_delete):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_delete.return_value = mock_response

        result = delete_incident(self.config, self.auth_manager, DeleteIncidentParams(incident_id=SYS_ID))

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Incident deleted successfully")
        self.assertEqual(result.incident_id, SYS_ID)
        self.assertTrue(mock_delete.call_args.args[0].endswith(f"/table/incident/{SYS_ID}"))

    @patch('requests.get')
    def test_get_incident_by_sys_id(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001",
                                                       "short_description": "x", "state": "1"}}
        mock_get.return_value = mock_response

        result = get_incident(self.config, self.auth_manager, GetIncidentParams(incident_id=SYS_ID))

        self.assertTrue(result["success"])
        self.assertEqual(result["incident"]["number"], "INC0010001")

    @patch('requests.get')
    def test_get_incident_returns_code_and_label(self, mock_get):
        """With display_value=all, state/priority come back as {value, display_value};
        the tool must expose the raw CODE plus a *_display label."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {
            "sys_id": {"value": SYS_ID, "display_value": SYS_ID},
            "number": {"value": "INC0010001", "display_value": "INC0010001"},
            "state": {"value": "6", "display_value": "Resolved"},
            "priority": {"value": "3", "display_value": "3 - Moderate"},
            "assigned_to": {"value": "abc", "display_value": "Admin User"},
        }}
        mock_get.return_value = mock_response

        incident = get_incident(self.config, self.auth_manager, GetIncidentParams(incident_id=SYS_ID))["incident"]

        self.assertEqual(incident["state"], "6")
        self.assertEqual(incident["state_display"], "Resolved")
        self.assertEqual(incident["priority"], "3")
        self.assertEqual(incident["assigned_to"], "Admin User")

    @patch('requests.put')
    def test_close_incident_sets_state_7(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_put.return_value = mock_response

        result = close_incident(self.config, self.auth_manager, CloseIncidentParams(
            incident_id=SYS_ID, close_code="Solution provided", close_notes="done"))

        self.assertTrue(result.success)
        sent = mock_put.call_args.kwargs["json"]
        self.assertEqual(sent["state"], "7")
        self.assertEqual(sent["close_code"], "Solution provided")
        self.assertNotIn("resolved_at", sent)

    @patch('requests.put')
    def test_reopen_incident_sets_state_2_with_note(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_put.return_value = mock_response

        result = reopen_incident(self.config, self.auth_manager, ReopenIncidentParams(
            incident_id=SYS_ID, reopen_notes="needs more work"))

        self.assertTrue(result.success)
        sent = mock_put.call_args.kwargs["json"]
        self.assertEqual(sent["state"], "2")
        self.assertEqual(sent["work_notes"], "needs more work")

    @patch('requests.post')
    def test_create_incident_sets_channel(self, mock_post):
        """channel maps to the ServiceNow contact_type field."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_post.return_value = mock_response

        result = create_incident(self.config, self.auth_manager, CreateIncidentParams(
            short_description="x", channel="phone"))

        self.assertTrue(result.success)
        self.assertEqual(mock_post.call_args.kwargs["json"]["contact_type"], "phone")

    @patch('requests.post')
    @patch('requests.get')
    def test_create_incident_resolves_caller_by_name(self, mock_get, mock_post):
        """A non-sys_id caller is resolved to a sys_id via a sys_user lookup."""
        lookup = MagicMock()
        lookup.status_code = 200
        lookup.json.return_value = {"result": [{"sys_id": "62826bf03710200044e0bfc8bcbe5df1"}]}
        mock_get.return_value = lookup
        created = MagicMock()
        created.status_code = 201
        created.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_post.return_value = created

        result = create_incident(self.config, self.auth_manager, CreateIncidentParams(
            short_description="x", caller_id="Abel Tuter"))

        self.assertTrue(result.success)
        self.assertIn("sys_user", mock_get.call_args.args[0])  # looked up a user
        self.assertEqual(mock_post.call_args.kwargs["json"]["caller_id"], "62826bf03710200044e0bfc8bcbe5df1")


    @patch('requests.get')
    def test_list_incidents_date_and_response_filters(self, mock_get):
        """list_incidents builds creation-date, between-dates and last-response
        (updated) filters, with the free-text OR grouped first."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_get.return_value = mock_response

        list_incidents(self.config, self.auth_manager, ListIncidentsParams(
            query="vpn", state="2", urgency="1", severity="2", impact="3", priority="1",
            created_after="2026-06-01", created_before="2026-06-30",
            updated_after="2026-06-15 09:00:00"))

        q = mock_get.call_args[1]["params"]["sysparm_query"]
        # free-text OR must come first so the trailing ANDs apply to the whole set
        self.assertTrue(q.startswith("short_descriptionLIKEvpn^ORdescriptionLIKEvpn^"))
        self.assertIn("state=2", q)
        # urgency / severity / impact / priority equality filters
        self.assertIn("urgency=1", q)
        self.assertIn("severity=2", q)
        self.assertIn("impact=3", q)
        self.assertIn("priority=1", q)
        # date-only bounds expand to start/end of day; full datetime passes through
        self.assertIn("sys_created_on>=2026-06-01 00:00:00", q)
        self.assertIn("sys_created_on<=2026-06-30 23:59:59", q)
        self.assertIn("sys_updated_on>=2026-06-15 09:00:00", q)

    @patch('requests.get')
    def test_list_incidents_filters_by_caller(self, mock_get):
        """caller_id is an equality filter on the caller's sys_id, ANDed after the free text."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_get.return_value = mock_response

        list_incidents(self.config, self.auth_manager, ListIncidentsParams(query="vpn", caller_id=SYS_ID))

        q = mock_get.call_args[1]["params"]["sysparm_query"]
        self.assertTrue(q.startswith("short_descriptionLIKEvpn^ORdescriptionLIKEvpn^"))
        self.assertIn(f"caller_id={SYS_ID}", q)

    @patch('requests.get')
    def test_list_incidents_without_caller_adds_no_caller_filter(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}
        mock_get.return_value = mock_response

        list_incidents(self.config, self.auth_manager, ListIncidentsParams())

        self.assertNotIn("caller_id", mock_get.call_args[1]["params"]["sysparm_query"])

    @patch('requests.get')
    def test_incident_format_includes_caller_opened_at_and_contact_type(self, mock_get):
        """New fields are added alongside the existing ones; nothing is removed or renamed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": [{
            "sys_id": {"value": SYS_ID, "display_value": SYS_ID},
            "number": {"value": "INC0010001", "display_value": "INC0010001"},
            "state": {"value": "2", "display_value": "In Progress"},
            "caller_id": {"value": "fedcba9876543210fedcba9876543210", "display_value": "Jane Tester"},
            "opened_at": {"value": "2026-06-01 09:30:00", "display_value": "2026-06-01 02:30:00"},
            "contact_type": {"value": "phone", "display_value": "Phone"},
        }]}
        mock_get.return_value = mock_response

        incident = list_incidents(self.config, self.auth_manager, ListIncidentsParams())["incidents"][0]

        self.assertEqual(incident["caller_id"], "fedcba9876543210fedcba9876543210")
        self.assertEqual(incident["caller_display"], "Jane Tester")
        self.assertEqual(incident["opened_at"], "2026-06-01 09:30:00")
        self.assertEqual(incident["contact_type"], "phone")
        for existing in ("sys_id", "number", "short_description", "description", "state",
                         "state_display", "priority", "priority_display", "assigned_to",
                         "category", "subcategory", "created_on", "updated_on"):
            self.assertIn(existing, incident)


    @patch('requests.put')
    def test_update_incident_puts_on_hold_with_reason(self, mock_put):
        """On Hold needs a hold_reason; it is sent alongside the state."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_put.return_value = mock_response

        result = update_incident(self.config, self.auth_manager,
                                 UpdateIncidentParams(incident_id=SYS_ID, state="3", hold_reason="1"))

        self.assertTrue(result.success)
        self.assertEqual(mock_put.call_args[1]["json"], {"state": "3", "hold_reason": "1"})

    @patch('requests.put')
    def test_update_incident_without_hold_reason_sends_none(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"sys_id": SYS_ID, "number": "INC0010001"}}
        mock_put.return_value = mock_response

        update_incident(self.config, self.auth_manager, UpdateIncidentParams(incident_id=SYS_ID, state="2"))

        self.assertNotIn("hold_reason", mock_put.call_args[1]["json"])

if __name__ == '__main__':
    unittest.main()
