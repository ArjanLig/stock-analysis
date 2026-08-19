"""Obsidian notes MCP -- multi-user, OAuth-brug naar Supabase Auth.

Dezelfde auth-opzet als de LazyTheta-server, met dezelfde mcp_auth-module uit
de repo-root. Wat verschilt is wat erachter zit: deze service leest notities
en heeft niets met beleggingsdata te maken. Dat is ook de reden dat het een
aparte service is -- schrijfrechten op de complete kennisbank is een veel
bredere bevoegdheid dan de portefeuilletools hebben.
"""

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_auth import (
    oauth_authorize,
    oauth_authorize_magic,
    oauth_authorize_password,
    oauth_magic_callback,
    oauth_magic_finalize,
    oauth_register,
    oauth_token,
    verify_jwt,
    well_known_authorization_server,
    well_known_protected_resource,
)
from mcp_handler import mcp_endpoint

PUBLIC_PREFIXES = ("/oauth/", "/.well-known/", "/health")


class SmartAuthMiddleware:
    """Publieke paden gaan door; al het andere vereist een Bearer-JWT. De
    user_id uit dat token belandt in scope, zodat handlers hem niet uit de
    argumenten hoeven te halen -- en dus ook niet kunnen."""

    def __init__(self, app):
        self.app = app

    async def _passthrough(self, scope, receive, send):
        try:
            return await self.app(scope, receive, send)
        except Exception:
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                await send({"type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"internal_server_error"}'})
            except Exception:
                pass

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await self._passthrough(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")

        if auth.startswith("Bearer "):
            payload = verify_jwt(auth[7:])
            if payload and payload.get("type") == "access_token" and payload.get("user_id"):
                scope.setdefault("state", {})["user_id"] = payload["user_id"]
                return await self._passthrough(scope, receive, send)

        host = headers.get(b"x-forwarded-host", headers.get(b"host", b"")).decode("latin-1")
        proto = headers.get(b"x-forwarded-proto", b"https").decode("latin-1")
        www_auth = (f'Bearer resource_metadata="{proto}://{host}'
                    f'/.well-known/oauth-protected-resource"')

        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"www-authenticate", www_auth.encode("latin-1"))]})
        await send({"type": "http.response.body", "body": b"Unauthorized"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "notes-mcp"})


def create_app():
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server",
              well_known_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              well_known_protected_resource, methods=["GET"]),
        Route("/oauth/register", oauth_register, methods=["POST"]),
        Route("/oauth/authorize", oauth_authorize, methods=["GET"]),
        Route("/oauth/authorize/magic", oauth_authorize_magic, methods=["POST"]),
        Route("/oauth/authorize/password", oauth_authorize_password, methods=["POST"]),
        Route("/oauth/magic-callback", oauth_magic_callback, methods=["GET"]),
        Route("/oauth/magic-finalize", oauth_magic_finalize, methods=["POST"]),
        Route("/oauth/token", oauth_token, methods=["POST"]),
        Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"]),
    ]
    return SmartAuthMiddleware(Starlette(routes=routes))


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
