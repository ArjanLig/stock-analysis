"""Stateless MCP JSON-RPC-dispatcher voor de Obsidian-vaults.

Elke /mcp-aanroep draagt een JWT; de middleware in main.py haalt daar de
user_id uit en zet hem in scope["state"]. Die user_id gaat naar elke tool.
Een user_id in de argumenten wordt genegeerd -- dat is de les uit het
load_credential-lek, waar een ongefilterde lezing de rijen van alle
gebruikers matchte.

Fase 1 leest alleen. Er is met opzet geen schrijftool.
"""

from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import notes_tools
from vault_paths import UnsafePath
from vault_storage import NoteNotFound, StorageUnavailable, VaultStore, make_client

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "notes-mcp"
SERVER_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

_CACHED_STORE = None


def _store() -> VaultStore:
    global _CACHED_STORE
    if _CACHED_STORE is None:
        _CACHED_STORE = VaultStore(make_client(),
                                   os.environ.get("VAULT_BUCKET", "vaults"))
    return _CACHED_STORE


async def _tool_list_vaults(user_id: str, args: dict):
    return notes_tools.list_vaults(_store(), user_id)


async def _tool_search_notes(user_id: str, args: dict):
    return notes_tools.search_notes(
        _store(), user_id, query=args["query"], vault=args.get("vault"),
        max_results=int(args.get("max_results", 20)))


async def _tool_read_note(user_id: str, args: dict):
    return notes_tools.read_note(_store(), user_id, args["vault"], args["path"])


TOOL_HANDLERS = {
    "list_vaults": _tool_list_vaults,
    "search_notes": _tool_search_notes,
    "read_note": _tool_read_note,
}

TOOLS: list[dict] = [
    {
        "name": "list_vaults",
        "description": (
            "List the Obsidian vaults, how many notes each holds, and whether "
            "it carries a CLAUDE.md with that vault's own conventions. Read "
            "that file before writing anything into a vault."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_notes",
        "description": (
            "Search note contents across the vaults. Returns the path plus the "
            "surrounding text of each match. Pass `vault` to narrow the search "
            "when you already know where to look."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "vault": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_note",
        "description": (
            "Read one note in full. `path` is relative to the vault root, for "
            "example 'Tickers/DECK.md'. Returns the content and a revision "
            "marker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["vault", "path"],
        },
    },
]


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        if not user_id:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32001, "message": "Authenticated user required"}}
        tool_name = params.get("name")
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}}
        try:
            result = await handler(user_id, params.get("arguments") or {})
        except (UnsafePath, NoteNotFound, KeyError, ValueError) as e:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                               "isError": True}}
        except StorageUnavailable as e:
            # Nadrukkelijk geen lege uitkomst: de vault is niet leeg, hij is
            # onbereikbaar, en dat verschil moet de aanroeper zien.
            logger.exception("storage unavailable")
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32002, "message": f"Vault storage unavailable: {e}"}}
        except Exception:
            logger.exception("Tool %s failed", tool_name)
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32603, "message": "Internal server error"}}

        import json
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(result, ensure_ascii=False)}]}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_endpoint(request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "GET not supported"}},
                            status_code=405)
    if request.method == "DELETE":
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)

    user_id = request.scope.get("state", {}).get("user_id")

    if isinstance(body, list):
        responses = [r for r in
                     [await _handle_one(m, user_id) for m in body] if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)

    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid request"}},
                            status_code=400)

    response = await _handle_one(body, user_id)
    return JSONResponse(response) if response is not None else Response(status_code=202)
