"""LazyTheta DCF MCP Server -- multi-user, OAuth bridge to Supabase Auth.

Routes (filled in across Tasks 2-4):
- /health                                    Liveness probe
- /mcp                                       MCP JSON-RPC (auth: JWT with user_id)
- /.well-known/oauth-authorization-server    OAuth metadata (Task 3)
- /.well-known/oauth-protected-resource      Resource metadata (Task 3)
- /oauth/register                            Dynamic Client Registration (Task 3)
- /oauth/authorize                           claude.ai entry -> Supabase login (Task 3)
- /oauth/magic-callback                      Supabase magic-link return (Task 3)
- /oauth/token                               claude.ai exchanges code for access token (Task 3)

Every authenticated request carries a JWT issued after a per-user Supabase
Auth flow. SmartAuthMiddleware extracts user_id from the JWT and stashes it
in scope["state"] for downstream handlers.
"""

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_auth import (
    SmartAuthMiddleware,
    oauth_authorize,
    oauth_authorize_magic,
    oauth_authorize_password,
    oauth_magic_callback,
    oauth_magic_finalize,
    oauth_register,
    oauth_token,
    well_known_authorization_server,
    well_known_protected_resource,
)
from mcp_handler import mcp_endpoint


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "lazytheta-mcp"})


def create_app():
    routes = [
        Route("/health", health, methods=["GET"]),
        Route(
            "/.well-known/oauth-authorization-server",
            well_known_authorization_server,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            well_known_protected_resource,
            methods=["GET"],
        ),
        Route("/oauth/register", oauth_register, methods=["POST"]),
        Route("/oauth/authorize", oauth_authorize, methods=["GET"]),
        Route("/oauth/authorize/magic", oauth_authorize_magic, methods=["POST"]),
        Route("/oauth/authorize/password", oauth_authorize_password, methods=["POST"]),
        Route("/oauth/magic-callback", oauth_magic_callback, methods=["GET"]),
        Route("/oauth/magic-finalize", oauth_magic_finalize, methods=["POST"]),
        Route("/oauth/token", oauth_token, methods=["POST"]),
        Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"]),
    ]
    starlette_app = Starlette(routes=routes)
    return SmartAuthMiddleware(starlette_app)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
