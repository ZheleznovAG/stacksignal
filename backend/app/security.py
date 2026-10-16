"""Browser-facing protection for a service with no login that is only safe because it listens on
127.0.0.1. Binding to localhost stops other machines, not other *websites*: a page open in the same
browser can still aim requests at http://127.0.0.1:8000. Three cheap checks close the usual routes:

- Host must be one of ALLOWED_HOSTS, which defeats DNS rebinding (an attacker's domain resolving to
  127.0.0.1 still carries its own name in Host);
- a state-changing request that comes from another site (Origin, or Sec-Fetch-Site) is refused, so a
  malicious page cannot quietly start runs, delete projects or send the digest;
- the dashboard may not be framed (clickjacking).

None of this is authentication. Before exposing StackSignal on a network, add auth first."""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def _host_name(value: str) -> str:
    """"localhost:8000" -> "localhost", "[::1]:8000" -> "::1"."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_hosts: set[str]):
        super().__init__(app)
        self.allowed = {_host_name(h) for h in allowed_hosts}

    async def dispatch(self, request, call_next):
        if _host_name(request.headers.get("host", "")) not in self.allowed:
            return PlainTextResponse("Invalid host header", status_code=400)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and _host_name(urlsplit(origin).netloc) not in self.allowed:
                return PlainTextResponse("Cross-site request refused", status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return PlainTextResponse("Cross-site request refused", status_code=403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
