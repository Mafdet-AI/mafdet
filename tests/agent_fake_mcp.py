"""A fake MCP server on stdio, for the agent's client: `count` tools named tool_0..., one of them
read-only, each answering with its name and arguments; `big` answers 20,000 characters."""
import json
import sys

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def tools():
    out = [{"name": f"tool_{i}", "title": "A title", "description": f"Does thing number {i}. " + "Details. " * 60,
            "inputSchema": {"$schema": "x", "type": "object", "additionalProperties": False,
                            "properties": {"title": {"type": "string", "title": "T", "description": "d " * 200}}},
            "annotations": {"readOnlyHint": i == 0}} for i in range(COUNT)]
    out.append({"name": "decompile_function", "description": "Decompile one function to pseudo code.",
                "inputSchema": {"type": "object", "properties": {"address": {"type": "string"}}}})
    out.append({"name": "big", "description": "A long output.", "inputSchema": {"type": "object"}})
    return out


for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}, "instructions": "Open a binary first."}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "big":
            result = {"content": [{"type": "text", "text": "line of output\n" * 1400}]}
        elif name == "tool_1":
            result = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
        else:
            result = {"content": [{"type": "text", "text": f"{name} {json.dumps(args, sort_keys=True)}"},
                                  {"type": "image", "data": "", "mimeType": "image/png"}]}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()
