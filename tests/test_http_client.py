"""Tests for the pooled HTTP client (servicenow_mcp.utils.http) and the optional ``fields`` parameter."""

import threading
from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests
from pydantic import ValidationError

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.incident_tools import ListIncidentsParams, list_incidents
from servicenow_mcp.tools.request_tools import ListRequestedItemsParams, list_requested_items
from servicenow_mcp.tools.user_tools import GetUserParams, get_user
from servicenow_mcp.utils import http
from servicenow_mcp.utils.api import fields_param
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig


@pytest.fixture
def config():
    auth = AuthConfig(type=AuthType.BASIC, basic=BasicAuthConfig(username="u", password="p"))
    return ServerConfig(instance_url="https://example.service-now.com", auth=auth, timeout=7)


@pytest.fixture
def auth_manager(config):
    return AuthManager(config.auth, config.instance_url)


def _ok(result):
    response = MagicMock()
    response.json.return_value = {"result": result}
    response.raise_for_status.return_value = None
    return response


# --- the client --------------------------------------------------------------------------------------


def test_same_session_within_a_thread_and_one_per_thread():
    http.reset()
    first, again = http.session(), http.session()
    assert first is again and isinstance(first, real_requests.Session)
    other = []
    t = threading.Thread(target=lambda: other.append(http.session()))
    t.start()
    t.join()
    assert other[0] is not first
    http.reset()
    assert http.session() is not first


def test_cookies_are_never_kept():
    http.reset()
    jar = http.session().cookies
    cookie = real_requests.cookies.create_cookie("JSESSIONID", "abc", domain="example.service-now.com")
    assert jar.get_policy().set_ok(cookie, None) is False


def test_functions_delegate_to_the_thread_session():
    http.reset()
    with patch.object(http.session(), "request", return_value="r") as request:
        assert http.get("https://x/a", params={"q": 1}, timeout=3) == "r"
        assert http.post("https://x/a", json={"a": 1}, timeout=3) == "r"
        assert http.patch("https://x/a", json={}, timeout=3) == "r"
        assert http.put("https://x/a", json={}, timeout=3) == "r"
        assert http.delete("https://x/a", timeout=3) == "r"
    methods = [c.args[0] for c in request.call_args_list]
    assert methods == ["GET", "POST", "PATCH", "PUT", "DELETE"]
    assert all(c.kwargs["timeout"] == 3 for c in request.call_args_list)
    http.reset()


def test_exceptions_are_the_requests_classes():
    assert http.RequestException is real_requests.RequestException
    assert http.exceptions.HTTPError is real_requests.exceptions.HTTPError


# --- fields ------------------------------------------------------------------------------------------


def test_fields_param():
    assert fields_param(None) == {} and fields_param([]) == {}
    assert fields_param(["sys_id", "caller_id.name"]) == {"sysparm_fields": "sys_id,caller_id.name"}


@pytest.mark.parametrize("model", [ListIncidentsParams, ListRequestedItemsParams, GetUserParams])
@pytest.mark.parametrize("bad", [["sys_id^ORactive=true"], ["Sys Id"], ["a,b"]])
def test_invalid_field_names_are_refused(model, bad):
    with pytest.raises(ValidationError):
        model(fields=bad)


@patch("servicenow_mcp.tools.incident_tools.requests.get")
def test_list_incidents_sends_sysparm_fields_only_when_asked(mock_get, config, auth_manager):
    mock_get.return_value = _ok([])
    list_incidents(config, auth_manager, ListIncidentsParams(caller_id="a" * 32))
    assert "sysparm_fields" not in mock_get.call_args.kwargs["params"]
    list_incidents(config, auth_manager, ListIncidentsParams(caller_id="a" * 32, fields=["sys_id", "number"]))
    params = mock_get.call_args.kwargs["params"]
    assert params["sysparm_fields"] == "sys_id,number" and params["sysparm_display_value"] == "all"
    assert mock_get.call_args.kwargs["timeout"] == 7


@patch("servicenow_mcp.tools.request_tools.requests.get")
def test_list_requested_items_sends_sysparm_fields(mock_get, config, auth_manager):
    mock_get.return_value = _ok([])
    list_requested_items(config, auth_manager, ListRequestedItemsParams(requested_for="b" * 32, fields=["sys_id"]))
    assert mock_get.call_args.kwargs["params"]["sysparm_fields"] == "sys_id"


@patch("servicenow_mcp.tools.user_tools.requests.get")
def test_get_user_sends_sysparm_fields(mock_get, config, auth_manager):
    mock_get.return_value = _ok([{"sys_id": "1"}])
    out = get_user(config, auth_manager, GetUserParams(employee_number="E-1", fields=["sys_id", "first_name"]))
    assert out["success"] is True
    assert mock_get.call_args.kwargs["params"]["sysparm_fields"] == "sys_id,first_name"
