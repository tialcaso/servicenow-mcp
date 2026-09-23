"""Tests for the Streamable HTTP transport.

Security (401/421/403) goes through TestClient. The end-to-end tests run the app on a real
uvicorn server in a background thread and talk to it with the SDK's own MCP client. No
ServiceNow instance is contacted: initialize and tools/list never call the ServiceNow API.
"""

import contextlib
import socket
import threading
import time

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from servicenow_mcp.server import ServiceNowMCP
from servicenow_mcp.server_http import (
    _resolve_auth_token,
    create_streamable_http_app,
    main,
    run_session_managers,
)
from servicenow_mcp.server_sse import _build_allowed_hosts, _build_allowed_origins
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

TOKEN = "test-token-abc123"
# TestClient sends `Host: testserver` by default; include it in allowlists.
ALLOWED_HOSTS = {"testserver", "127.0.0.1:8080", "localhost:8080", "[::1]:8080"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS} | {f"https://{h}" for h in ALLOWED_HOSTS}

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def _reset_sse_starlette_exit_event():
    """sse-starlette keeps a process-global anyio.Event bound to the first event loop that
    waits on it. Each TestClient / uvicorn server here runs its own loop, so reset it before
    each one (the workaround sse-starlette documents for test suites)."""
    AppStatus.should_exit_event = None


@pytest.fixture(autouse=True)
def _fresh_sse_starlette_state():
    _reset_sse_starlette_exit_event()
    yield
    _reset_sse_starlette_exit_event()


def _servicenow_mcp(monkeypatch, package):
    """A ServiceNowMCP with the given MCP_TOOL_PACKAGE (read at construction time)."""
    monkeypatch.setenv("MCP_TOOL_PACKAGE", package)
    config = ServerConfig(
        instance_url="https://example.service-now.com",
        auth=AuthConfig(
            type=AuthType.BASIC, basic=BasicAuthConfig(username="user", password="pass")
        ),
    )
    return ServiceNowMCP(config)


def _expected_tool_count(snow):
    exposed = [n for n in snow.enabled_tool_names if n in snow.tool_definitions]
    return len(exposed) + 1  # + list_tool_packages, always exposed


def _build_app(snow, **kwargs):
    kwargs.setdefault("auth_token", TOKEN)
    kwargs.setdefault("allowed_hosts", ALLOWED_HOSTS)
    kwargs.setdefault("allowed_origins", ALLOWED_ORIGINS)
    return create_streamable_http_app(snow.mcp_server, **kwargs)


def _bearer(tok=TOKEN):
    return {"Authorization": f"Bearer {tok}"}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _loopback_allowlists(port):
    hosts = _build_allowed_hosts("127.0.0.1", port)
    return hosts, _build_allowed_origins(hosts)


@contextlib.contextmanager
def _serve(app, port):
    """Run app on uvicorn in a background thread (lifespan on) until the block exits."""
    _reset_sse_starlette_exit_event()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _initialize_and_list_tools(url, token=TOKEN):
    """Real MCP client: initialize, then tools/list. Returns (server name, tool names)."""

    async def run():
        async with httpx.AsyncClient(headers=_bearer(token), timeout=10) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    return init.serverInfo.name, [t.name for t in tools.tools]

    return anyio.run(run)


# --- 1. Bearer-token auth --------------------------------------------------


def test_no_authorization_header_returns_401(monkeypatch):
    client = TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk")))
    r = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_wrong_token_returns_401(monkeypatch):
    client = TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk")))
    r = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **_bearer("wrong")})
    assert r.status_code == 401


def test_wrong_scheme_returns_401(monkeypatch):
    client = TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk")))
    r = client.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Authorization": f"Basic {TOKEN}"}
    )
    assert r.status_code == 401


def test_correct_token_reaches_mcp_endpoint(monkeypatch):
    with TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk"))) as client:
        r = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **_bearer()})
    assert r.status_code == 200
    assert '"serverInfo"' in r.text
    assert '"ServiceNow"' in r.text


def test_json_response_mode_returns_plain_json(monkeypatch):
    app = _build_app(_servicenow_mcp(monkeypatch, "service_desk"), json_response=True)
    with TestClient(app) as client:
        r = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **_bearer()})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["result"]["serverInfo"]["name"] == "ServiceNow"


def test_empty_auth_token_rejected(monkeypatch):
    with pytest.raises(ValueError):
        _build_app(_servicenow_mcp(monkeypatch, "service_desk"), auth_token="")


# --- 2. Host / Origin allowlist (same codes as SSE) ------------------------


def test_host_not_in_allowlist_returns_421(monkeypatch):
    client = TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk")))
    r = client.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **_bearer(), "Host": "evil.example"}
    )
    assert r.status_code == 421


def test_origin_not_in_allowlist_returns_403(monkeypatch):
    client = TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk")))
    r = client.post(
        "/mcp",
        json=INITIALIZE,
        headers={**MCP_HEADERS, **_bearer(), "Origin": "https://attacker.example"},
    )
    assert r.status_code == 403


def test_allowed_origin_passes(monkeypatch):
    with TestClient(_build_app(_servicenow_mcp(monkeypatch, "service_desk"))) as client:
        r = client.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, **_bearer(), "Origin": "http://127.0.0.1:8080"},
        )
    assert r.status_code == 200


# --- 3. Real MCP client over uvicorn: tool package decides tools/list ------


def test_real_client_lists_tools_of_configured_package(monkeypatch):
    counts = {}
    for package in ("service_desk", "catalog_builder"):
        snow = _servicenow_mcp(monkeypatch, package)
        port = _free_port()
        hosts, origins = _loopback_allowlists(port)
        app = _build_app(snow, allowed_hosts=hosts, allowed_origins=origins)
        with _serve(app, port) as base:
            server_name, tools = _initialize_and_list_tools(f"{base}/mcp")
        assert server_name == "ServiceNow"
        assert "list_tool_packages" in tools
        assert len(tools) == _expected_tool_count(snow)
        counts[package] = len(tools)
    assert counts["service_desk"] != counts["catalog_builder"]


def test_real_client_rejected_with_wrong_token(monkeypatch):
    snow = _servicenow_mcp(monkeypatch, "service_desk")
    port = _free_port()
    hosts, origins = _loopback_allowlists(port)
    app = _build_app(snow, allowed_hosts=hosts, allowed_origins=origins)
    with _serve(app, port) as base:
        with pytest.raises(Exception):
            _initialize_and_list_tools(f"{base}/mcp", token="wrong")


# --- 4. Mounted under a sub-path of a parent app, parent lifespan ----------


def test_mounted_in_parent_app_with_parent_lifespan(monkeypatch):
    snow_a = _servicenow_mcp(monkeypatch, "service_desk")
    snow_b = _servicenow_mcp(monkeypatch, "knowledge_author")
    port = _free_port()
    hosts, origins = _loopback_allowlists(port)
    profile_a = _build_app(snow_a, allowed_hosts=hosts, allowed_origins=origins, path="/")
    profile_b = _build_app(snow_b, allowed_hosts=hosts, allowed_origins=origins, path="/")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with run_session_managers(profile_a, profile_b):
            yield

    parent = Starlette(
        routes=[Mount("/mcp/profile-a", app=profile_a), Mount("/mcp/profile-b", app=profile_b)],
        lifespan=lifespan,
    )
    with _serve(parent, port) as base:
        _, tools_a = _initialize_and_list_tools(f"{base}/mcp/profile-a/")
        _, tools_b = _initialize_and_list_tools(f"{base}/mcp/profile-b/")
        r = httpx.post(f"{base}/mcp/profile-a/", json=INITIALIZE, headers=MCP_HEADERS)
    assert len(tools_a) == _expected_tool_count(snow_a)
    assert len(tools_b) == _expected_tool_count(snow_b)
    assert len(tools_a) != len(tools_b)
    assert r.status_code == 401  # mounted app keeps its own auth


# --- 5. main() / token guards (same as SSE) --------------------------------


def test_main_allow_remote_without_token_exits(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0", "--allow-remote"])


def test_main_remote_bind_without_allow_remote_exits(monkeypatch):
    monkeypatch.delenv("MCP_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def test_main_allow_remote_via_env_still_requires_token(monkeypatch):
    monkeypatch.setenv("MCP_ALLOW_REMOTE", "1")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def test_resolve_auth_token_autogen_on_loopback(monkeypatch, capsys):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    tok = _resolve_auth_token(allow_remote=False)
    assert len(tok) >= 32
    assert "[servicenow-mcp-http] generated auth token" in capsys.readouterr().err


def test_create_streamable_http_app_no_debug_by_default(monkeypatch):
    assert _build_app(_servicenow_mcp(monkeypatch, "service_desk")).debug is False
