"""MCP servers as tools: the decompiler, the debugger, the device are somebody else's server, and
the agent is a client (stdio transport, JSON-RPC, nothing to install).

Servers are named in ~/.mafdet/mcp.json, in the shape other MCP clients use:

    {"mcpServers": {"rea": {"command": "npx", "args": ["-y", "rea-agents", "mcp"],
                            "tools": ["open_binary", "procedure_*"],
                            "allow": ["list_*", "search_*", "procedure_*", "xrefs"]}}}

`tools` are the ones the model starts with, `allow` the ones that run without asking (name
patterns, both): a server's tool asks first unless the server marks it read-only, and few do.

A reverse-engineering server can list two hundred tools, and their schemas alone would not fit in
the context. So only some are given to the model (`tools` in the file, --tools, or a --profile),
with their descriptions cut short, and `find_tools` lets the model look among the others and turn
on the ones it needs. Turning tools on changes the start of the prompt, which is read again: worth
it a few times in a session, not at every step.
"""
from __future__ import annotations

import fnmatch
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from .tools import Tool

PROTOCOL = "2025-06-18"
DESCRIPTION_CHARS = 200
PARAMETER_CHARS = 100
TOOL_CHARS = 450                # a tool's description and schema together, about 150 tokens
# Schema keywords the model does not need to write a call: names, and checks the server makes anyway.
DROPPED = {"$schema", "$id", "$comment", "title", "additionalProperties", "examples", "pattern", "format",
           "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "deprecated"}

# Tools by task, as glob patterns on their names (any server's). The first is for static analysis of
# a native binary: open it, look around, read a function, follow references, write names back.
PROFILES = {
    "native-static": ["open_binary", "close_binary", "binary_overview", "list_segments", "list_procedures",
                      "search_procedures", "procedure_info", "procedure_pseudo_code", "procedure_assembly",
                      "procedure_callers", "procedure_callees", "search_strings", "xrefs", "read_bytes",
                      "set_address_name", "set_comment"],
}


class McpError(RuntimeError):
    pass


def configured() -> dict:
    """The servers of ~/.mafdet/mcp.json."""
    from ..core import config
    servers = config.read_json("mcp.json").get("mcpServers")
    return servers if isinstance(servers, dict) else {}


class McpServer:
    def __init__(self, name: str, command: str, args: list[str] | tuple[str, ...] = (), env: dict | None = None,
                 cwd: str | None = None, log: str | None = None):
        self.name = name
        exe = shutil.which(command) or command               # npx is npx.cmd on Windows
        self._log = open(log, "w", encoding="utf-8") if log else None
        try:
            self.proc = subprocess.Popen(
                [exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log or subprocess.DEVNULL, cwd=cwd, env={**os.environ, **(env or {})},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as e:
            raise McpError(f"MCP server {name}: cannot start `{command}`: {e}") from None
        self._next = 0
        self._waiting: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "mafdet", "version": "0"}}, timeout=120)
        self.instructions = str(info.get("instructions") or "")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict):
        try:
            with self._lock:
                self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
        except (OSError, ValueError):
            raise McpError(f"MCP server {self.name} has stopped") from None

    def _read(self):
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue                                     # a server that prints something else on stdout
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:              # the server asks us something
                answer = ({"result": {}} if msg["method"] == "ping" else
                          {"error": {"code": -32601, "message": "not supported by this client"}})
                try:
                    self._send({"jsonrpc": "2.0", "id": msg["id"], **answer})
                except McpError:
                    break
            elif "id" in msg:
                q = self._waiting.pop(msg["id"], None)
                if q is not None:
                    q.put(msg)
        for q in list(self._waiting.values()):               # the server is gone: nobody waits forever
            q.put({"error": {"message": "the server stopped"}})

    def request(self, method: str, params: dict | None = None, timeout: float = 300.0) -> dict:
        self._next += 1
        rid, q = self._next, queue.Queue()
        self._waiting[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            self._waiting.pop(rid, None)
            raise McpError(f"MCP server {self.name}: no answer to {method} in {timeout:.0f} s") from None
        if msg.get("error"):
            raise McpError(f"MCP server {self.name}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=120)
            tools += page.get("tools") or []
            cursor = page.get("nextCursor")
            if not cursor:
                return tools

    def call(self, name: str, args: dict, timeout: float = 300.0) -> str:
        result = self.request("tools/call", {"name": name, "arguments": args}, timeout=timeout)
        parts = []
        for item in result.get("content") or []:
            kind = item.get("type")
            if kind == "text":
                parts.append(item.get("text", ""))
            elif kind == "resource" and (item.get("resource") or {}).get("text"):
                parts.append(item["resource"]["text"])
            else:
                parts.append(f"[{kind} left out: this model reads text only]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        text = "\n".join(parts) or "[no output]"
        return "error: " + text if result.get("isError") else text

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        if self._log:
            self._log.close()


def _slim(schema):
    """A tool's schema without what the model does not need (titles, $schema, long descriptions)."""
    if isinstance(schema, list):
        return [_slim(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in DROPPED:
            continue
        if k == "description" and isinstance(v, str):
            v = _short(v, PARAMETER_CHARS)
        # the keys of `properties` are parameter names, not schema keywords: one may be called "title"
        out[k] = {name: _slim(p) for name, p in v.items()} if k == "properties" and isinstance(v, dict) else _slim(v)
    return out


def _flat(prop, chars: int):
    """One parameter down to what a call needs: its type, a short enum, a short description."""
    if not isinstance(prop, dict):
        return prop
    for key in ("anyOf", "oneOf"):                  # "a string or null": the first real type
        options = [o for o in prop.get(key) or [] if isinstance(o, dict) and o.get("type") != "null"]
        if options:
            prop = {**options[0], **{k: v for k, v in prop.items() if k not in ("anyOf", "oneOf")}}
            break
    out = {}
    if "type" in prop:
        out["type"] = prop["type"]
    if isinstance(prop.get("enum"), list) and len(prop["enum"]) <= 8:
        out["enum"] = prop["enum"]
    if isinstance(prop.get("items"), dict) and "type" in prop["items"]:
        out["items"] = {"type": prop["items"]["type"]}
    if chars and isinstance(prop.get("description"), str):
        out["description"] = _short(prop["description"], chars)
    return out or {"type": "string"}


def _budget(description: str, schema: dict) -> tuple[str, dict]:
    """Cut a tool's schema, a level at a time, until it is under TOOL_CHARS or bare: sixteen tools of a
    reverse-engineering server took 3.8k tokens with their schemas only slimmed, a quarter of 16k.
    Nothing a call needs goes: every parameter keeps its name and type, `required` stays."""
    def size() -> int:
        return len(description) + len(json.dumps(schema, ensure_ascii=False))

    props = schema.get("properties")
    if size() <= TOOL_CHARS or not isinstance(props, dict):
        return description, schema
    required = set(schema.get("required") or [])
    schema = {k: v for k, v in schema.items() if k in ("type", "properties", "required")}
    for level in (60, 0):                           # nested schemas flattened; then the optional ones bare
        schema["properties"] = {name: _flat(p, 60 if (name in required or level) else 0)
                                for name, p in props.items()}
        if size() <= TOOL_CHARS:
            return description, schema
    description = _short(description, 110)
    if size() > TOOL_CHARS:                         # as bare as it gets: a tool of many parameters stays above
        schema["properties"] = {name: _flat(p, 0) for name, p in props.items()}
    return description, schema


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def as_tool(server: McpServer, spec: dict, name: str = "", allow: list[str] | tuple[str, ...] = ()) -> Tool:
    """A server's tool as one of the agent's. It runs without asking when the server marks it read-only,
    or when the user listed it under `allow` in mcp.json; anything else asks."""
    remote = spec["name"]
    schema = _slim(spec.get("inputSchema") or {}) or {"type": "object", "properties": {}}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    read_only = (bool((spec.get("annotations") or {}).get("readOnlyHint"))
                 or any(fnmatch.fnmatch(remote, p) for p in allow))
    description, schema = _budget(_short(spec.get("description") or remote, DESCRIPTION_CHARS), schema)
    return Tool(name or remote, description, schema,
                lambda args: server.call(remote, args), "read" if read_only else "exec", source=server.name)


class Toolbox:
    """The tools the model is given now (`active`), and the ones it can look for (`hidden`)."""

    def __init__(self, tools: list[Tool] = ()):
        self.active: dict[str, Tool] = {t.name: t for t in tools}
        self.hidden: dict[str, Tool] = {}

    def add_server(self, server: McpServer, patterns: list[str] | None = None,
                   allow: list[str] | tuple[str, ...] = ()):
        """The server's tools: active when their name matches one of `patterns` (all of them when
        there are none and the server is small), the rest reachable through find_tools. The ones
        matching `allow` run without asking."""
        specs = server.list_tools()
        if patterns is None:
            patterns = ["*"] if len(specs) <= 12 else []
        for spec in specs:
            name = spec["name"]
            if name in self.active or name in self.hidden or name == "find_tools":
                name = f"{server.name}_{name}"
            tool = as_tool(server, spec, name, allow)
            where = self.active if any(fnmatch.fnmatch(spec["name"], p) for p in patterns) else self.hidden
            where[name] = tool
        self._finder()

    def _finder(self):
        if not self.hidden:
            self.active.pop("find_tools", None)
            return
        self.active["find_tools"] = Tool(
            "find_tools", f"Look among {len(self.hidden)} more tools by what they do, and turn some on.",
            {"type": "object", "required": [],
             "properties": {"query": {"type": "string", "description": "words describing what is needed"},
                            "enable": {"type": "array", "items": {"type": "string"},
                                       "description": "names of tools to turn on"}}},
            self.find)

    def find(self, args: dict) -> str:
        out = []
        for name in args.get("enable") or []:
            tool = self.hidden.pop(str(name), None)
            if tool is None:
                out.append(f"{name}: " + ("already on" if name in self.active else "no such tool"))
            else:
                self.active[tool.name] = tool
                out.append(f"{name}: on, call it from the next step")
        words = str(args.get("query") or "").lower().split()
        if words:
            scored = []
            for tool in self.hidden.values():
                text = f"{tool.name} {tool.description}".lower()
                score = sum(3 if w in tool.name.lower() else 1 for w in words if w in text)
                if score:
                    scored.append((-score, tool.name, tool))
            out += [f"{t.name}: {_short(t.description, 140)}" for _, _, t in sorted(scored)[:8]]
            if not scored:
                out.append("no tool matches: try other words")
            elif not args.get("enable"):
                out.append("Turn on the ones needed with find_tools(enable=[names]).")
        self._finder()
        return "\n".join(out) or "give a query, or names to enable"

    def get(self, name: str) -> Tool | None:
        return self.active.get(name)

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.active.values()]


def log_path(session_dir: Path, name: str) -> str:
    return str(session_dir / f"mcp-{''.join(c if c.isalnum() else '_' for c in name)}.log")
