"""Minimal MCP (Model Context Protocol) stdio server for the Teams exporter.

Exposes three tools over newline-delimited JSON-RPC on stdio:
  - teams_status
  - teams_list_chats
  - teams_export_chat

No third-party dependencies. Register it with any MCP client, e.g.:
  command: <python>
  args: ["<repo>/mcp_server.py"]
"""

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import teams_web_export as tw

VERSION = "1.0.0"

TOOLS = [
    {
        "name": "teams_status",
        "description": "Check whether a saved Microsoft Teams session exists and when it expires. Call this before exporting.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "teams_list_chats",
        "description": "List the signed-in user's personal Teams chats with title, conversation id, last message time and member count.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "teams_export_chat",
        "description": "Export one Teams chat's full message history plus shared files and images to JSON, CSV and optional ZIP. Returns file paths and counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat": {
                    "type": "string",
                    "description": "Chat title substring (case-insensitive) or full conversation id starting with '19:'.",
                },
                "out": {
                    "type": "string",
                    "description": "Output folder. Defaults to <repo>/exports.",
                },
                "media": {
                    "type": "boolean",
                    "description": "Download shared files/images. Default true.",
                },
                "zip": {
                    "type": "boolean",
                    "description": "Also write a .zip archive of the export folder. Default false.",
                },
                "include_system": {
                    "type": "boolean",
                    "description": "Include join/leave/rename events. Default false.",
                },
                "include_events": {
                    "type": "boolean",
                    "description": "Include call started/ended events. Default false.",
                },
            },
            "required": ["chat"],
            "additionalProperties": False,
        },
    },
]


def quiet_call(function, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        return function(*args, **kwargs)


def call_tool(name, arguments):
    tw.JSON_MODE = True
    if name == "teams_status":
        result = tw.command_status()
    elif name == "teams_list_chats":
        result = quiet_call(tw.command_list)
    elif name == "teams_export_chat":
        chat = (arguments.get("chat") or "").strip()
        if not chat:
            raise ValueError("'chat' is required")
        out = arguments.get("out") or str(tw.EXPORT_DIR)
        result = quiet_call(
            tw.command_export,
            chat,
            out,
            bool(arguments.get("include_system", False)),
            bool(arguments.get("include_events", False)),
            bool(arguments.get("media", True)),
            bool(arguments.get("zip", False)),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")
    return result


def respond(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def respond_error(message_id, code, message):
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message},
    }) + "\n")
    sys.stdout.flush()


def handle(request):
    method = request.get("method")
    message_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        respond(message_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "teams-chat-exporter", "version": VERSION},
        })
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "ping":
        respond(message_id, {})
    elif method == "tools/list":
        respond(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments)
            respond(message_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            })
        except Exception as exc:
            respond(message_id, {
                "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(exc)})}],
                "isError": True,
            })
    elif message_id is not None:
        respond_error(message_id, -32601, f"Method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            respond_error(None, -32700, "Parse error")
            continue
        if isinstance(request, list):
            for item in request:
                handle(item)
        else:
            handle(request)


if __name__ == "__main__":
    main()
