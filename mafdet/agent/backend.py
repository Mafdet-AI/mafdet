"""A decompiler's MCP server as the five operations the walk needs (`walk.py`): list the functions,
the callees of one, its pseudo-code, and, when the server can, rename it and comment it; and one
optional operation for the second pass (--revise): the binary's strings, for the program's summary.
An optional operation may be given as a list of candidates: the first tool the server lists that its
schema lets us call is taken (`Adapter._candidate`).

Each server spells them differently, so a server is described as data: for each operation, the
tool, its arguments (templates: "{address}", "{name}", "{comment}", "{path}", "{basename}",
"{sha1_6}" (the first 6 hex digits of the file's sha1) and "{binary}", the program's name in a
project server) and where the answer is in the result (`path`, keys and list indexes). The table is
picked from the tools the connected server lists, not from its name. Results are read defensively:
an MCP result may carry `structuredContent`, JSON in its text, or plain text; rea wraps every answer
in an evidence envelope (`normalized_result`), FastMCP servers wrap a list in {"result": [...]}.

Both tables were checked on a real server with Ghidra 12.1.4 on a PE (where.exe, 2026-11-12):

    rea           rea-agents 6.9.0 (6.7.0 answers the same, without `symbol`). Addresses "0x140001010",
                  imports "EXTERNAL:0x13" named "MSVCRT.DLL::memset" (the code says `memset`); callees
                  are addresses; the pseudo-code uses the listing's names (FUN_...). Ghidra analyses on
                  the first query (list_procedures: 20-25 s here); then every answer is precomputed
                  (about 1 ms). The project is ephemeral, and on Windows rea writes nothing ("Windows
                  Ghidra P0 does not admit database mutation"): binary_session says which tools are
                  available, and the walk does not try the others.
    pyghidra-mcp  pyghidra-mcp 0.3.0. A persistent Ghidra project: names and comments stay, and show at
                  once in the callers' pseudo-code. Addresses "140004a04" (no 0x); imports are not
                  listed (callees name them: SetLastError); callees are names, from decompile_function
                  (so fetching them decompiles). import_binary works in the background: the program
                  shows in list_project_binaries ("/where.exe-e4c967": name-sha1[:6]) once imported,
                  analysis_complete once analysed. Importing a program already there is skipped (on
                  Windows pyghidra-mcp would try again and fail on the open file). Changes are on disk
                  only after `save`, or a clean shutdown the client may not wait for: the walk saves.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

# Names a decompiler gives a function it knows nothing about. Anything else (imports, exports,
# symbols, a name the analyst gave) is a real name: the walk records it as known and does not ask
# the model about it.
DEFAULT_NAMES = (
    r"sub_[0-9A-Fa-f]+",                        # IDA, Hopper, rea
    r"FUN_[0-9A-Fa-f]+",                        # Ghidra
    r"fcn\.[0-9A-Fa-f]+",                       # radare2 / rizin
    r"thunk_(?:FUN|sub)_[0-9A-Fa-f]+",          # a thunk to a function with no name
    r"j_sub_[0-9A-Fa-f]+",                      # IDA's jump thunks
    r"nullsub_[0-9]+",                          # IDA's empty functions
)
DEFAULT_RX = re.compile("|".join(f"(?:{p})" for p in DEFAULT_NAMES))
DEFAULT_IN_TEXT = re.compile(r"(?<![\w.])(?:" + "|".join(DEFAULT_NAMES) + r")(?![\w.])")


def is_default(name: str) -> bool:
    return bool(DEFAULT_RX.fullmatch(name or ""))


def default_address(name: str) -> int | None:
    """The address a default name carries (sub_401a30 -> 0x401a30), when it carries one."""
    m = re.search(r"(?:sub_|FUN_|fcn\.)([0-9A-Fa-f]+)$", name or "")
    return int(m.group(1), 16) if m and is_default(name) else None


def addr_int(text) -> int | None:
    """An address as a number, whatever the backend's spelling: 0x401A30, 00401a30, 401a30h, 4200000."""
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    s = str(text or "").strip().lower()
    if s.endswith("h") and len(s) > 1:
        s = s[:-1]
    if s.startswith("0x"):
        s = s[2:]
    if s.startswith("ram:"):                    # Ghidra's address space prefix
        s = s[4:]
    return int(s, 16) if s and len(s) <= 16 and all(c in "0123456789abcdef" for c in s) else None


REA = {
    "name": "rea",
    "source": "rea-agents 6.9.0 / 6.7.0 with Ghidra 12.1.4, checked on a PE (2026-11-12)",
    "open": {"tool": "open_binary", "args": {"path": "{path}"}, "timeout": 600},
    # Which tools this host can run (the writes cannot, on Windows), asked once the binary is open.
    "availability": {"tool": "binary_session", "args": {}, "path": ["tool_availability", "groups"]},
    "close": {"tool": "close_binary", "args": {}},
    # The first query waits for Ghidra's whole auto-analysis.
    "list": {"tool": "list_procedures", "args": {}, "timeout": 3600},
    "callees": {"tool": "procedure_callees", "args": {"procedure": "{address}"}},
    "code": {"tool": "procedure_pseudo_code", "args": {"procedure": "{address}"}, "timeout": 600},
    "rename": {"tool": "set_address_name", "args": {"address": "{address}", "name": "{name}"}},
    "comment": {"tool": "set_comment", "args": {"address": "{address}", "comment": "{comment}"}},
    # Optional, for the program's summary (--revise): candidates, the first the server has and can be called
    # with these arguments (those its schema does not name are dropped). Checked on rea 6.9.0 (where.exe,
    # 2026-11-13): list_strings takes no limit or offset (dropped) and gives all 153 at once, ~38 s when it is
    # the first query (it waits for the analysis), 0.02 s after; search_strings needs a non-empty pattern
    # (minLength 1): "." as a regex gives the same 150.
    "strings": [{"tool": "list_strings", "args": {"limit": 400, "offset": 0}, "timeout": 900},
                {"tool": "search_strings", "args": {"pattern": ".", "mode": "regex"}, "timeout": 900}],
}

PYGHIDRA = {
    "name": "pyghidra-mcp",
    "source": "pyghidra-mcp 0.3.0 with Ghidra 12.1.4, checked on a PE (2026-11-12)",
    # Imported (and analysed) in the background, and only when the project does not have it yet.
    "open": {"tool": "import_binary", "args": {"binary_path": "{path}"}, "timeout": 600, "unless_listed": True},
    # The program's name in the project, for `binary_name`: waited for while it is imported and analysed.
    "context": {"tool": "list_project_binaries", "args": {}, "path": ["programs"], "as": "binary",
                "match": "{basename}-{sha1_6}", "ready": "analysis_complete"},
    "list": {"tool": "search_symbols_by_name", "path": ["symbols"], "timeout": 900,
             "args": {"binary_name": "{binary}", "query": ".*", "functions_only": True, "offset": 0,
                      "limit": 1000000}},
    # No callee tool: decompile_function gives the callees' names (it decompiles, so it costs).
    "callees": {"tool": "decompile_function", "path": [0, "callees"], "timeout": 300,
                "args": {"binary_name": "{binary}", "name_or_address": "{address}", "include_callees": True,
                         "timeout_sec": 120}},
    "code": {"tool": "decompile_function", "path": [0, "code"], "timeout": 600,
             "args": {"binary_name": "{binary}", "name_or_address": "{address}", "timeout_sec": 300}},
    "rename": {"tool": "rename_function",
               "args": {"binary_name": "{binary}", "name_or_address": "{address}", "new_name": "{name}"}},
    "comment": {"tool": "set_comment", "args": {"binary_name": "{binary}", "target": "{address}",
                                                "comment": "{comment}", "comment_type": "plate"}},
    "save": {"tool": "save", "args": {}, "timeout": 600},
    # As rea's: from pyghidra-mcp's documentation, NOT checked on a real server (what an empty query returns).
    "strings": [{"tool": "search_strings", "args": {"binary_name": "{binary}", "query": "", "limit": 400},
                 "timeout": 300}],
}

TABLES = (REA, PYGHIDRA)
NEEDED = ("list", "callees", "code")
OPTIONAL = ("open", "rename", "comment", "strings")


class BackendError(RuntimeError):
    pass


# ---------------------------------------------------------------- reading results

def payload(result: dict):
    """What a tools/call result says, as data: structuredContent, else JSON in the text, else the text."""
    if not isinstance(result, dict):
        return unwrap(result)
    if result.get("structuredContent") is not None:
        return unwrap(result["structuredContent"])
    texts = [str(item.get("text") or "") for item in result.get("content") or []
             if isinstance(item, dict) and item.get("type") == "text"]
    if len(texts) > 1:                          # FastMCP: one text item per element of a list
        try:
            return unwrap([json.loads(t) for t in texts])
        except ValueError:
            pass
    return unwrap("\n".join(texts))


def unwrap(data):
    for _ in range(6):
        if isinstance(data, str) and data.strip()[:1] in ("[", "{"):
            try:
                data = json.loads(data)
                continue
            except ValueError:
                return data
        if isinstance(data, dict):
            if "normalized_result" in data:     # rea's evidence envelope
                data = data["normalized_result"]
                continue
            if set(data) == {"result"}:         # FastMCP's wrapping of a list or a scalar
                data = data["result"]
                continue
        break
    return data


def dig(data, path):
    """Follow `path` (keys and indexes) into data, forgiving a list of one where an object was meant
    and an object where a list was meant."""
    for key in path or ():
        if isinstance(key, int):
            if isinstance(data, list):
                data = data[key] if -len(data) <= key < len(data) else None
            continue                            # an object already: the list was not there
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            if data.get("error"):
                raise BackendError(str(data["error"]))
            data = data.get(key)
        else:
            return data if isinstance(data, list) else None
    if isinstance(data, dict) and data.get("error") and set(data) <= {"error", "name", "target"}:
        raise BackendError(str(data["error"]))
    return data


ADDR_KEYS = ("address", "entrypoint", "entry_point", "entry", "addr", "start", "offset", "ea")
NAME_KEYS = ("name", "value", "label", "symbol", "function_name", "func_name")
ADDR_TOKEN = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9a-fA-F]{6,16})$")


def _first(item: dict, keys):
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip() or isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def _flag(item: dict, *names) -> bool:
    for where in (item, item.get("symbol"), item.get("procedure"), item.get("classification")):
        if isinstance(where, dict):
            for n in names:
                if where.get(n):
                    return True
    return False


def _line(line: str):
    """(address, name) from a line of plain text: "0x401000 sub_401000", "main @ 0x401000", "401000: main"."""
    tokens = [t.strip(",;:|()[]") for t in line.replace("@", " ").replace("\t", " ").split()]
    tokens = [t for t in tokens if t and t not in ("-", "—", "=", "=>", "->")]
    addr = next((t for t in tokens if ADDR_TOKEN.match(t) and (t.startswith("0x") or not t.isalpha())), None)
    name = next((t for t in tokens if t != addr and re.match(r"[A-Za-z_.$?@]", t)), None)
    return addr, name


def functions(data) -> list[dict]:
    """The functions of a listing: [{"address", "name", "external", "thunk", "source", "size"}]."""
    items = data
    if isinstance(data, dict):
        for key in ("functions", "procedures", "symbols", "items", "results", "names"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        else:
            if data and all(isinstance(v, str) for v in data.values()):
                items = [{"address": k, "name": v} for k, v in data.items()]
    if isinstance(items, str):
        items = [line for line in items.splitlines() if line.strip()]
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            addr, name = _first(item, ADDR_KEYS), _first(item, NAME_KEYS)
            symbol = item.get("symbol") if isinstance(item.get("symbol"), dict) else {}
            size = item.get("size") or item.get("length")
            entry = {"address": hex(addr) if isinstance(addr, int) else addr, "name": name,
                     "external": _flag(item, "external"), "thunk": _flag(item, "thunk", "is_thunk"),
                     "source": str(item.get("source") or symbol.get("source") or ""),
                     "size": size if isinstance(size, (int, float)) and not isinstance(size, bool) else None}
        elif isinstance(item, str):
            addr, name = _line(item)
            entry = {"address": addr, "name": name, "external": False, "thunk": False, "source": "", "size": None}
        else:
            continue
        if entry["address"] is None and entry["name"]:
            n = default_address(entry["name"])
            entry["address"] = hex(n) if n is not None else None
        if entry["address"] is not None:
            entry["address"] = str(entry["address"])
            entry["name"] = str(entry["name"] or "")
            out.append(entry)
    return out


def refs(data) -> list[str]:
    """Callees as strings, each an address or a name."""
    if isinstance(data, dict):
        for key in ("callees", "functions", "procedures", "calls", "items", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if isinstance(data, str):
        data = [t for line in data.splitlines() for t in [line.strip(" -*\t")] if t]
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            v = _first(item, ADDR_KEYS) or _first(item, NAME_KEYS)
            if v is not None:
                out.append(hex(v) if isinstance(v, int) else str(v))
        elif isinstance(item, (str, int)) and not isinstance(item, bool):
            out.append(hex(item) if isinstance(item, int) else item.strip())
    return [x for x in out if x]


STRING_KEYS = ("value", "string", "text", "content", "str", "data")


def strings(data) -> list[str]:
    """String literals of a listing, whatever its shape: a list of strings, of {value|string|text, address},
    an object holding such a list, or plain lines."""
    if isinstance(data, dict):
        for key in ("strings", "items", "results", "matches", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if isinstance(data, str):
        data = data.splitlines()
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            item = _first(item, STRING_KEYS)
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.replace("\r\n", "\n")           # Ghidra's decompiler writes CRLF on Windows
    if isinstance(data, list):
        return text(data[0]) if data else ""
    if isinstance(data, dict):
        for key in ("code", "pseudo_code", "pseudocode", "decompiled", "decompilation", "text", "c", "source"):
            if isinstance(data.get(key), str):
                return text(data[key])
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------- the adapter

def pick(tool_names) -> dict | None:
    names = set(tool_names)
    for table in TABLES:
        if all(table[op]["tool"] in names for op in NEEDED):
            return table
    return None


class Adapter:
    """One connected server, spoken to through its table."""

    def __init__(self, server, table: dict, specs: list[dict], binary: Path):
        self.server = server
        self.name = table["name"]
        self.specs = {s.get("name"): s for s in specs if isinstance(s, dict)}
        self.table = {op: self._candidate(spec) if isinstance(spec, list) else spec for op, spec in table.items()}
        self.table = {op: spec for op, spec in self.table.items() if spec is not None}
        self.vars = {"path": str(binary), "basename": binary.name, "binary": binary.name, "sha1_6": ""}
        if "{sha1_6}" in json.dumps(table) and binary.is_file():
            h = hashlib.sha1()
            with open(binary, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            self.vars["sha1_6"] = h.hexdigest()[:6]
        self.unavailable: dict[str, str] = {}       # tool -> why this host cannot run it
        self.unsaved = 0                            # writes since the last save

    @classmethod
    def connect(cls, server, binary: Path) -> "Adapter":
        specs = server.list_tools()
        table = pick(s.get("name") for s in specs)
        if table is None:
            wanted = "; ".join(f"{t['name']}: " + ", ".join(t[op]["tool"] for op in NEEDED) for t in TABLES)
            raise BackendError(f"the server {server.name} has none of the tool sets the walk knows ({wanted})")
        return cls(server, table, specs, Path(binary).resolve())

    def _candidate(self, specs: list[dict]) -> dict | None:
        """An optional operation given as candidates: the first tool the server lists whose required
        arguments the template gives, with the arguments its schema does not name dropped."""
        for spec in specs:
            tool = self.specs.get(spec["tool"])
            if tool is None:
                continue
            schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            args = {k: v for k, v in spec["args"].items() if not props or k in props}
            if all(r in args for r in schema.get("required") or []):
                return {**spec, "args": args}
        return None

    def has(self, op: str) -> bool:
        return op in self.table and self.table[op]["tool"] in self.specs and self.table[op]["tool"] not in self.unavailable

    def why_not(self, op: str) -> str:
        if op not in self.table or self.table[op]["tool"] not in self.specs:
            return "the server has no such tool"
        tool = self.table[op]["tool"]
        return f"{tool}: {self.unavailable[tool]}" if tool in self.unavailable else ""

    def operations(self) -> str:
        """What this server can do for the walk, said once the binary is open."""
        parts = [f"{op} ({self.table[op]['tool']})" if self.has(op) else
                 f"{op}: no" + (f" ({self.unavailable[self.table[op]['tool']]})"
                                if self.table[op]["tool"] in self.unavailable else "")
                 for op in NEEDED + OPTIONAL if op in self.table]
        return ", ".join(parts)

    def _args(self, op: str, values: dict) -> dict:
        env = {**self.vars, **values}
        out = {}
        for k, v in self.table[op]["args"].items():
            if isinstance(v, str):
                whole = re.fullmatch(r"\{(\w+)\}", v)
                v = env.get(whole.group(1), "") if whole else v.format(**env)
            out[k] = v
        return out

    def raw(self, op: str, **values) -> dict:
        spec = self.table[op]
        result = self.server.request("tools/call", {"name": spec["tool"], "arguments": self._args(op, values)},
                                     timeout=spec.get("timeout", 300))
        if isinstance(result, dict) and result.get("isError"):
            data = payload({**result, "isError": False})
            raise BackendError(f"{spec['tool']}: {str(data if isinstance(data, str) else json.dumps(data))[:300]}")
        return result

    def call(self, op: str, **values):
        return dig(payload(self.raw(op, **values)), self.table[op].get("path"))

    # the five operations, and opening
    def open(self, say=lambda text: None, stop=None, ready_timeout: float = 1800.0, poll: float = 5.0):
        listed = None
        if self.table.get("open", {}).get("unless_listed") and self._context_tool():
            try:
                listed = self._program(self._programs())
            except BackendError:
                listed = None
            if listed is not None:
                say(f"open: {self._name(listed)} is in the project already, not imported again")
        if self.has("open") and listed is None:
            try:
                self.raw("open")
            except BackendError as e:                   # already open, already in the project: go on
                say(f"open: {e}")
        if self._context_tool():
            self._context(say, stop, ready_timeout, poll)
        if "availability" in self.table:
            self._availability()

    def _context_tool(self) -> bool:
        return "context" in self.table and self.table["context"]["tool"] in self.specs

    def _programs(self) -> list:
        items = self.call("context")
        return items if isinstance(items, list) else []

    @staticmethod
    def _name(item) -> str:
        return str((item.get("name") if isinstance(item, dict) else item) or "")

    def _program(self, items: list):
        """This binary's program among a project's: by its exact name, with or without a folder."""
        want = self.table["context"]["match"].format(**self.vars).lower()
        for item in items:
            name = self._name(item).lower()
            if name == want or name.rsplit("/", 1)[-1] == want:
                return item
        return None

    def _context(self, say, stop, ready_timeout: float, poll: float = 5.0):
        """Wait until the program is in the project (an import runs in the background) and analysed."""
        spec = self.table["context"]
        want = spec["match"].format(**self.vars)
        deadline = time.monotonic() + ready_timeout
        said = ""
        while True:
            items = self._programs()
            chosen = self._program(items)
            ready = spec.get("ready")
            if chosen is not None:
                self.vars[spec["as"]] = self._name(chosen)
                if not ready or not isinstance(chosen, dict) or ready not in chosen or chosen.get(ready):
                    return
                state = f"{self.vars[spec['as']]}: waiting for the analysis to complete..."
            else:
                state = f"waiting for {want} to be imported..."
            if time.monotonic() > deadline or (stop is not None and stop.is_set()):
                if chosen is None:
                    raise BackendError(f"{spec['tool']}: no program {want} among "
                                       f"{[self._name(i) for i in items][:10]} after {ready_timeout:.0f} s")
                say(f"{self.vars[spec['as']]}: analysis not complete, going on anyway")
                return
            if state != said:
                say(state)
                said = state
            if stop is not None:
                stop.wait(poll)
            else:
                time.sleep(poll)

    def _availability(self):
        """The tools the server says it cannot run here (rea: no writes on Windows), so they are not tried."""
        try:
            groups = self.call("availability")
        except Exception:                               # an old server, a tool gone: assume all available
            return
        for g in groups if isinstance(groups, list) else []:
            if isinstance(g, dict) and g.get("available") is False:
                why = " — ".join(str(x) for x in (g.get("reason"), g.get("remediation")) if x) or "not available"
                for n in g.get("names") or []:
                    self.unavailable[str(n)] = why

    def functions(self) -> list[dict]:
        return functions(self.call("list"))

    def callees(self, address: str) -> list[str]:
        return refs(self.call("callees", address=address))

    def code(self, address: str) -> str:
        return text(self.call("code", address=address))

    def strings(self) -> list[str]:
        """The binary's string literals, when the server lists them (optional: [] when it cannot)."""
        return strings(self.call("strings")) if self.has("strings") else []

    def write(self, op: str, **values) -> str:
        """rename / comment: "" when done, else why not. Never raises."""
        try:
            data = self.call(op, **values)
        except Exception as e:                          # BackendError, McpError, a server gone
            return str(e)[:300] or type(e).__name__
        if data is False:
            return "the server says the edit did not take (readback did not match)"
        self.unsaved += 1
        return ""

    def save(self) -> str:
        """The writes made so far onto disk, when the server keeps them only in memory until asked
        (pyghidra-mcp: `save`; a client that stops it may not wait for its own save on exit).
        "" when done or nothing to do, else why not. Never raises."""
        if not self.unsaved or not self.has("save"):
            return ""
        try:
            self.raw("save")
        except Exception as e:
            return str(e)[:300] or type(e).__name__
        self.unsaved = 0
        return ""

    def close(self):
        self.save()
        if self.has("close"):
            try:
                self.raw("close")
            except Exception:
                pass
