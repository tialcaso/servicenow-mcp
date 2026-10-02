"""HTTP client with connection reuse for every call to the ServiceNow instance.

The tools used to call ``requests.get/post/...`` directly, which opens a new TCP + TLS connection (and
builds a new SSL context) for every request. Against a remote instance that costs about one second per
call. This module exposes the same functions and exception classes as ``requests``, backed by one
``requests.Session`` per thread, so each thread keeps its connections to the instance alive.

Modules import it as ``from servicenow_mcp.utils import http as requests``: call sites and the tests
that patch ``<module>.requests.get`` keep working unchanged.

- One session per thread: ``requests.Session`` is not documented as thread-safe, and servers often call
  the tools from a thread pool.
- Cookies are not kept: every request authenticates on its own (Basic/OAuth/API key headers), exactly
  as before, so two different credentials in the same process never share a ServiceNow session.
- Timeouts and every other argument are passed through untouched (the caller still sets ``timeout``).
"""

import http.cookiejar
import threading

import requests as _requests
from requests import exceptions  # noqa: F401  (re-exported: callers use requests.exceptions.*)
from requests import (  # noqa: F401  (re-exported, same classes as the requests package)
    ConnectionError,
    HTTPError,
    RequestException,
    Response,
    Timeout,
)
from requests.adapters import HTTPAdapter

POOL_CONNECTIONS = 4   # distinct hosts kept per thread
POOL_MAXSIZE = 8       # connections kept per host per thread

_local = threading.local()


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request):  # never store a cookie from the instance
        return False


def session() -> _requests.Session:
    """The calling thread's session (created on first use)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = _requests.Session()
        s.cookies.set_policy(_NoCookies())
        adapter = HTTPAdapter(pool_connections=POOL_CONNECTIONS, pool_maxsize=POOL_MAXSIZE)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _local.session = s
    return s


def reset() -> None:
    """Close and forget the calling thread's session (tests, or after a fork)."""
    s = getattr(_local, "session", None)
    if s is not None:
        s.close()
        _local.session = None


def request(method, url, **kwargs):
    return session().request(method, url, **kwargs)


def get(url, params=None, **kwargs):
    return session().get(url, params=params, **kwargs)


def post(url, data=None, json=None, **kwargs):
    return session().post(url, data=data, json=json, **kwargs)


def put(url, data=None, **kwargs):
    return session().put(url, data=data, **kwargs)


def patch(url, data=None, **kwargs):
    return session().patch(url, data=data, **kwargs)


def delete(url, **kwargs):
    return session().delete(url, **kwargs)
