"""Minimal stdio MCP server for local agent tooling.

It intentionally exposes read/chat operations and the same explicit action
approval boundary as HTTP. Start it with ``python -m app.mcp_server`` from the
repository root; credentials remain in the local backend process.
"""

import asyncio
import json
import sys
from typing import Any

from .config import get_settings
from .main import create_app


TOOLS = [
    {"name": "dafeiyu_health", "description": "Read local service health", "inputSchema": {"type": "object"}},
    {"name": "dafeiyu_chat", "description": "Send a message to the local agent", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
    {"name": "dafeiyu_candidates", "description": "List discovered candidates", "inputSchema": {"type": "object"}},
    {"name": "dafeiyu_actions", "description": "List audited actions", "inputSchema": {"type": "object"}},
]


async def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "dafeiyu-agent", "version": "0.1.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    params = request.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    app = create_app(get_settings())
    try:
        store = app.state.store
        if name == "dafeiyu_health":
            result: Any = {"status": "ok", "mock_mode": app.state.provider.settings.mock_mode}
        elif name == "dafeiyu_candidates":
            result = [item.model_dump() for item in store.list_candidates(limit=100)]
        elif name == "dafeiyu_actions":
            result = [item.model_dump() for item in store.list_actions(limit=100)]
        elif name == "dafeiyu_chat":
            message = str(args.get("message", "")).strip()
            if not message:
                raise ValueError("message is required")
            conversation_id = str(args.get("conversation_id") or "mcp")
            candidate, _ = await app.state.agent.run(conversation_id, message, args.get("context"))
            result = {"conversation_id": conversation_id, "message": candidate.content}
        else:
            raise ValueError("Unknown tool")
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
    finally:
        store.close()


async def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = await handle(json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
