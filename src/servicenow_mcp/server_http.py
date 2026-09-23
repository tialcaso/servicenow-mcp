"""ServiceNow MCP Streamable HTTP server, with the same security contract as SSE.

Bearer-token auth and the Host/Origin allowlist come from ``server_sse.SecurityMiddleware``.
Defaults to loopback bind. Remote bind requires --allow-remote and an explicit
MCP_AUTH_TOKEN. See README "Streamable HTTP" for details.

Stateless by default: every POST is an independent JSON-RPC exchange, so the server can sit
behind a load balancer and clients that only run ``initialize`` + ``tools/list`` need no session.
"""

import argparse
import contextlib
import os
import secrets
import sys
from typing import AsyncIterator, Dict, List, Optional, Set, Union

import uvicorn
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from servicenow_mcp.server import ServiceNowMCP
from servicenow_mcp.server_sse import (
    _TRUTHY,
    SecurityMiddleware,
    _build_allowed_hosts,
    _build_allowed_origins,
    _is_loopback_host,
)
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig


def _resolve_auth_token(*, allow_remote: bool) -> str:
    tok = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if tok:
        return tok
    if allow_remote:
        raise SystemExit("MCP_AUTH_TOKEN must be set when --allow-remote is used")
    tok = secrets.token_urlsafe(32)
    print(f"[servicenow-mcp-http] generated auth token: {tok}", file=sys.stderr, flush=True)
    return tok


class _SessionManagerEndpoint:
    """ASGI endpoint that hands every request to the session manager."""

    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)


def create_streamable_http_app(
    mcp_server: Server,
    *,
    auth_token: str,
    allowed_hosts: Set[str],
    allowed_origins: Set[str],
    path: str = "/mcp",
    stateless: bool = True,
    json_response: bool = False,
    debug: bool = False,
) -> Starlette:
    """Build a mountable Starlette app exposing the MCP server via Streamable HTTP.

    The endpoint lives at ``path`` and is gated by the same SecurityMiddleware as SSE. The
    session manager is at ``app.state.session_manager``; the app's own lifespan runs it, so
    ``uvicorn.run(app)`` works as is.

    Mounted inside another app, the sub-app's lifespan does not run (Starlette only runs the
    outer one), so the parent must run the session manager itself. Use
    ``run_session_managers``:

        ```python
        import contextlib
        from fastapi import FastAPI

        profile_a = create_streamable_http_app(
            mcp_a.mcp_server, auth_token=token, allowed_hosts=hosts,
            allowed_origins=origins, path="/",
        )

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with run_session_managers(profile_a):
                yield

        app = FastAPI(lifespan=lifespan)
        app.mount("/mcp/profile-a", profile_a)  # endpoint: /mcp/profile-a/
        ```

    With ``path="/"`` the endpoint is the mount point plus a trailing slash; a request without
    the slash gets a 307 redirect from Starlette.

    The SDK's own DNS-rebinding check is left off (``security_settings=None``): Host and Origin
    are already enforced by SecurityMiddleware, before the request reaches the transport.
    """
    if not auth_token:
        raise ValueError("auth_token must be a non-empty string")

    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=json_response,
        stateless=stateless,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(
        debug=debug,
        routes=[Route(path, endpoint=_SessionManagerEndpoint(session_manager))],
        middleware=[
            Middleware(
                SecurityMiddleware,
                token=auth_token,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
            ),
        ],
        lifespan=lifespan,
    )
    app.state.session_manager = session_manager
    return app


@contextlib.asynccontextmanager
async def run_session_managers(*apps: Starlette) -> AsyncIterator[None]:
    """Run the session manager of each app built by create_streamable_http_app.

    For use inside the lifespan of a parent app that mounts them. Each session manager can
    only be run once, so an app must not be both mounted here and served on its own.
    """
    async with contextlib.AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.state.session_manager.run())
        yield


class ServiceNowStreamableHTTPMCP(ServiceNowMCP):
    """ServiceNow MCP server bound to a Streamable HTTP transport."""

    def __init__(self, config: Union[Dict, ServerConfig]):
        super().__init__(config)

    def start(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        allow_remote: bool = False,
        auth_token: Optional[str] = None,
        allowed_hosts: Optional[Set[str]] = None,
        allowed_origins: Optional[Set[str]] = None,
        path: str = "/mcp",
        debug: bool = False,
    ):
        """Start the Streamable HTTP server.

        host/port: bind address. Defaults to loopback; non-loopback requires allow_remote.
        auth_token: bearer token; auto-generated on loopback if None, required for remote.
        allowed_hosts/origins: allowlists; auto-built from host/port if None.
        """
        if not _is_loopback_host(host) and not allow_remote:
            raise SystemExit(
                f"refusing to bind non-loopback host {host!r} without allow_remote=True"
            )
        if auth_token is None:
            auth_token = _resolve_auth_token(allow_remote=allow_remote)
        if allowed_hosts is None:
            allowed_hosts = _build_allowed_hosts(host, port, [])
        if allowed_origins is None:
            allowed_origins = _build_allowed_origins(allowed_hosts)

        app = create_streamable_http_app(
            self.mcp_server,
            auth_token=auth_token,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            path=path,
            debug=debug,
        )
        uvicorn.run(app, host=host, port=port)


def create_servicenow_mcp(instance_url: str, username: str, password: str):
    """Create a ServiceNow MCP Streamable HTTP server with basic-auth ServiceNow credentials.

    Example:
        ```python
        mcp = create_servicenow_mcp(
            instance_url="https://instance.service-now.com",
            username="admin",
            password="password",
        )
        mcp.start()  # binds 127.0.0.1:8080/mcp with auto-generated bearer token
        ```
    """
    auth_config = AuthConfig(
        type=AuthType.BASIC, basic=BasicAuthConfig(username=username, password=password)
    )
    config = ServerConfig(instance_url=instance_url, auth=auth_config)
    return ServiceNowStreamableHTTPMCP(config)


def main(argv: Optional[List[str]] = None):
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run ServiceNow MCP Streamable HTTP server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1; non-loopback requires --allow-remote)",
    )
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        default=os.getenv("MCP_ALLOW_REMOTE", "").strip().lower() in _TRUTHY,
        help="Permit non-loopback bind (requires MCP_AUTH_TOKEN). Env: MCP_ALLOW_REMOTE",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        help="Extra Host header value to allow (repeatable). Env: MCP_ALLOWED_HOSTS=h1,h2",
    )
    args = parser.parse_args(argv)

    if not _is_loopback_host(args.host) and not args.allow_remote:
        parser.error(
            f"refusing to bind non-loopback host {args.host!r} without --allow-remote"
        )

    auth_token = _resolve_auth_token(allow_remote=args.allow_remote)

    extra_hosts: List[str] = list(args.allowed_host or [])
    env_hosts = os.getenv("MCP_ALLOWED_HOSTS", "")
    if env_hosts:
        extra_hosts.extend(h.strip() for h in env_hosts.split(",") if h.strip())

    allowed_hosts = _build_allowed_hosts(args.host, args.port, extra_hosts)
    allowed_origins = _build_allowed_origins(allowed_hosts)

    debug = os.getenv("SERVICENOW_DEBUG", "").strip().lower() in _TRUTHY

    server = create_servicenow_mcp(
        instance_url=os.getenv("SERVICENOW_INSTANCE_URL"),
        username=os.getenv("SERVICENOW_USERNAME"),
        password=os.getenv("SERVICENOW_PASSWORD"),
    )
    server.start(
        host=args.host,
        port=args.port,
        allow_remote=args.allow_remote,
        auth_token=auth_token,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        debug=debug,
    )


if __name__ == "__main__":
    main()
