"""Obsidian notes MCP -- multi-user, OAuth-brug naar Supabase Auth.

Dezelfde auth-opzet als de LazyTheta-server, met dezelfde mcp_auth-module uit
de repo-root. Wat verschilt is wat erachter zit: deze service leest notities
en heeft niets met beleggingsdata te maken. Dat is ook de reden dat het een
aparte service is -- schrijfrechten op de complete kennisbank is een veel
bredere bevoegdheid dan de portefeuilletools hebben.

SmartAuthMiddleware komt uit mcp_auth en is dus letterlijk dezelfde code als
bij LazyTheta. Een tweede kopie van de code die bepaalt óf je binnenkomt en
welke user_id je krijgt is precies wat het verhuizen van mcp_auth naar de
repo-root moest voorkomen.
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
