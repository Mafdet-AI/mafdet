"""A fake decompiler MCP server on stdio, for the walk (`tests/test_agent_walk.py`), over a small
synthetic call graph: a leaf alone, a chain, a diamond, a 2-cycle, self-recursion, an import and one
huge function; and a few strings (list_strings, search_strings: shapes guessed, not seen on a real
server). Its other answers have the shapes the real servers gave on a PE (Ghidra 12.1.4, 2026-11-12):

    python agent_fake_re.py rea        rea-agents 6.9.0: an evidence envelope in structuredContent (and as
                                       JSON in the text); addresses 0x1000, the import EXTERNAL:0x1 named
                                       MSVCRT.DLL::printf, callees as addresses, CRLF pseudo-code using the
                                       listing's FUN_ names; binary_session says which tools can run
    python agent_fake_re.py rea-text   the same envelope as JSON in the text only, callees as names
    python agent_fake_re.py pyghidra   pyghidra-mcp 0.3.0: FastMCP results, addresses 00001000, imports not
                                       listed (callees name them), callees from decompile_function; the
                                       program appears in the project after import_binary, then analysed

Environment: FAKE_RE_LOG, a file where every tools/call is appended as a JSON line; FAKE_RE_FAIL,
comma-separated tool names that answer with an error; FAKE_RE_NO_WRITE=1, rea says (as on Windows)
that it cannot rename or comment; FAKE_RE_PROJECT, a JSON file keeping pyghidra's project between runs;
FAKE_RE_ENTRY=1, the program has `entry` (a real name, code of its own, as the PE's entry point) calling the root.
"""
import hashlib
import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "rea"
LOG = os.environ.get("FAKE_RE_LOG")
FAIL = set(filter(None, os.environ.get("FAKE_RE_FAIL", "").split(",")))
NO_WRITE = os.environ.get("FAKE_RE_NO_WRITE") == "1"
PROJECT = os.environ.get("FAKE_RE_PROJECT")
IMPORT = 0x7000

# address -> callees
GRAPH = {
    0x1000: [0x2000, 0x3000, 0x6000, 0x8000, 0x9000, IMPORT],     # the root
    0x2000: [0x2100],                                              # a chain
    0x2100: [0x2200],
    0x2200: [],                                                    # its leaf
    0x3000: [0x3100, 0x3200],                                      # a diamond
    0x3100: [0x3300],
    0x3200: [0x3300],
    0x3300: [IMPORT],                                              # calls the import
    0x4000: [],                                                    # a leaf nobody calls
    0x6000: [0x6100],                                              # a 2-cycle
    0x6100: [0x6000, 0x2200],
    0x8000: [0x8000],                                              # self-recursion
    0x9000: [0x2200],                                              # huge
}
STRINGS = ["%d\n", "ok", "Usage: target [/s] pattern...", "target.cfg", "Adds the numbers it is given."]
names = {a: f"FUN_{a:08x}" for a in GRAPH}
if os.environ.get("FAKE_RE_ENTRY") == "1":
    GRAPH[0x500] = [0x1000]
    names[0x500] = "entry"
comments = {}


def listed_name(a):
    return "MSVCRT.DLL::printf" if a == IMPORT else names[a]


def code_name(a):
    return "printf" if a == IMPORT else names[a]


def addr(a):
    if MODE == "pyghidra":
        return f"{a:08x}"
    return "EXTERNAL:0x1" if a == IMPORT else hex(a)


def find(ref):
    ref = str(ref)
    if ref in ("EXTERNAL:0x1", "MSVCRT.DLL::printf"):
        return IMPORT
    for a, n in names.items():
        if ref == n:
            return a
    try:
        return int(ref, 16)
    except ValueError:
        return None


def code(a):
    lines = ["", "/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */", ""]
    if a in comments:
        lines[1:1] = [f"/* {comments[a]} */", ""]
    lines += [f"int {names[a]}(int param_1)", "", "{"]
    for c in GRAPH[a]:
        lines.append(f"  param_1 = {code_name(c)}(param_1);")
    if a == 0x9000:
        lines += [f"  param_1 = param_1 * {i} + 0x{i:x}; // filler line {i}" for i in range(2000)]
    lines += ["  return param_1;", "}", "", ""]
    return "\r\n".join(lines)


def text_result(data, structured=True):
    out = {"content": [{"type": "text", "text": json.dumps(data)}]}
    if structured:
        out["structuredContent"] = data
    return out


def error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ---------------------------------------------------------------- rea

def envelope(operation, result):
    return {"evidence_id": "ev_" + "0" * 64, "subject": {"name": "target.exe", "format": "pe"},
            "provider": {"id": "ghidra", "name": "Ghidra", "version": "12.1.4"}, "predicate_type": "rea.analysis",
            "operation": operation, "parameters": {}, "raw_result": result, "normalized_result": result,
            "confidence": "observed", "limitations": ["The imported Program and temporary project are ephemeral."]}


def rea_answer(operation, result):
    return text_result(envelope(operation, result), structured=MODE != "rea-text")


def rea_tools():
    p = {"procedure": {"type": "string", "description": "The procedure name or address"},
         "document": {"type": "string"}}
    ro = {"readOnlyHint": True}
    return [{"name": "open_binary", "inputSchema": {"type": "object", "required": ["path"],
                                                    "properties": {"path": {"type": "string"}}}},
            {"name": "close_binary", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "binary_session", "annotations": ro, "inputSchema": {"type": "object", "properties": {}}},
            {"name": "list_procedures", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}}}},
            {"name": "procedure_callees", "inputSchema": {"type": "object", "required": ["procedure"], "properties": p}},
            {"name": "procedure_callers", "inputSchema": {"type": "object", "required": ["procedure"], "properties": p}},
            {"name": "procedure_pseudo_code", "inputSchema": {"type": "object", "required": ["procedure"], "properties": p}},
            {"name": "procedure_info", "inputSchema": {"type": "object", "required": ["procedure"], "properties": p}},
            {"name": "set_address_name", "annotations": {"destructiveHint": True},
             "inputSchema": {"type": "object", "required": ["address", "name"],
                             "properties": {"address": {"type": "string"}, "name": {"type": "string"}}}},
            {"name": "set_comment", "annotations": {"destructiveHint": True},
             "inputSchema": {"type": "object", "required": ["address", "comment"],
                             "properties": {"address": {"type": "string"}, "comment": {"type": "string"}}}},
            {"name": "list_strings", "annotations": ro,
             "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}, "document": {"type": "string"}}}},
            {"name": "frida_attach", "inputSchema": {"type": "object", "properties": {}}}]


def rea_session():
    groups = [{"names": ["list_procedures", "procedure_callees", "procedure_pseudo_code"], "available": True,
               "reason": "available"}]
    if NO_WRITE:
        groups += [{"names": ["annotate_native_function", "set_address_name", "set_addresses_names"],
                    "available": False, "reason": "unsupported_host",
                    "remediation": "Windows Ghidra P0 does not admit database mutation."},
                   {"names": ["comment", "set_comment", "set_inline_comment"], "available": False,
                    "reason": "provider_missing",
                    "remediation": "Install or configure a provider that declares this operation."}]
    else:
        groups.append({"names": ["set_address_name", "set_comment"], "available": True, "reason": "available"})
    return text_result({"result": {"open": True, "tool_availability": {"groups": groups}}})


def rea_call(name, args):
    if name == "open_binary":
        return text_result({"result": {"path": args["path"], "format": "pe", "kind": "executable",
                                       "sha256": "0" * 64, "architecture": "x86_64"}})
    if name == "close_binary":
        return text_result({"result": None})
    if name == "binary_session":
        return rea_session()
    if name == "list_strings":
        return rea_answer(name, [{"address": hex(0x9000 + i), "value": s} for i, s in enumerate(STRINGS)])
    if name == "list_procedures":
        items = [{"address": addr(IMPORT), "value": listed_name(IMPORT),
                  "symbol": {"primary": True, "dynamic": False, "external": True, "type": "function", "source": "imported"},
                  "procedure": {"external": True, "thunk": False, "thunk_target": None}}]
        items += [{"address": addr(a), "value": names[a],
                   "symbol": {"primary": True, "dynamic": False, "external": False, "type": "function",
                              "source": "default" if names[a].startswith("FUN_") else "user_defined"},
                   "procedure": {"external": False, "thunk": False, "thunk_target": None}} for a in sorted(GRAPH)]
        return rea_answer(name, items)
    a = find(args.get("procedure") or args.get("address"))
    if a != IMPORT and a not in GRAPH:
        return error(f"no procedure {args}")
    if name == "procedure_callees":
        return rea_answer(name, [] if a == IMPORT else
                          [listed_name(c) if MODE == "rea-text" else addr(c) for c in GRAPH[a]])
    if name == "procedure_pseudo_code":
        return rea_answer(name, None if a == IMPORT else code(a))
    if name in ("set_address_name", "set_comment") and NO_WRITE:
        return error(f"{name} is not available on this host: Windows Ghidra P0 does not admit database mutation.")
    if name == "set_address_name":
        names[a] = args["name"]
        return rea_answer(name, True)
    if name == "set_comment":
        comments[a] = args["comment"]
        return rea_answer(name, True)
    return error("not here")


# ---------------------------------------------------------------- pyghidra-mcp

def load_project():
    if PROJECT and os.path.exists(PROJECT):
        with open(PROJECT, encoding="utf-8") as f:
            return json.load(f)
    return {"programs": [{"name": "/other.dll-1a2b3c", "file_path": "/C:/other.dll", "analysis_complete": True}]}


project = load_project()
importing = []          # [program, lists to answer still "importing", lists to answer "analysing"]


def store_project():
    if PROJECT:
        with open(PROJECT, "w", encoding="utf-8") as f:
            json.dump(project, f)


def fast(result):
    """FastMCP: a model as structuredContent, a list wrapped as {"result": [...]} with one text item per element."""
    if isinstance(result, list):
        return {"content": [{"type": "text", "text": json.dumps(x, indent=2)} for x in result],
                "structuredContent": {"result": result}}
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}], "structuredContent": result}


def program_infos():
    if importing:
        program, quiet, busy = importing[0]
        if quiet:
            importing[0][1] -= 1
        elif busy:
            importing[0][2] -= 1
            return project["programs"] + [{**program, "analysis_complete": False}]
        else:
            project["programs"].append(program)
            importing.clear()
            store_project()
    return [{"name": p["name"], "file_path": p["file_path"], "load_time": 1.0, "analysis_complete": p["analysis_complete"],
             "metadata": {}, "code_indexed": False, "strings_indexed": False} for p in project["programs"]]


def pyghidra_tools():
    b = {"binary_name": {"type": "string"}}
    return [{"name": n, "inputSchema": {"type": "object", "properties": b}} for n in
            ("decompile_function", "search_symbols_by_name", "list_project_binaries", "rename_function", "save",
             "set_comment", "list_imports", "list_xrefs", "import_binary", "search_strings")]


def pyghidra_call(name, args):
    if name == "import_binary":
        path = args["binary_path"]
        with open(path, "rb") as f:
            program = {"name": f"/{os.path.basename(path)}-{hashlib.sha1(f.read()).hexdigest()[:6]}",
                       "file_path": "/" + path.replace("\\", "/"), "analysis_complete": True}
        if not any(p["name"] == program["name"] for p in project["programs"]):
            importing[:] = [[program, 1, 1]]           # one look: not there yet; one more: not analysed
        return fast({"requested_path": path, "queued_count": 1, "queued_paths": [path], "skipped_count": 0,
                     "skipped": [], "message": f"Queued 1 import(s) from {path} in the background."})
    if name == "list_project_binaries":
        return fast({"programs": program_infos()})
    if name == "save":
        return fast({})
    wanted = str(args.get("binary_name"))
    mine = [p for p in project["programs"] if wanted in (p["name"], p["name"].lstrip("/"))]
    if not mine or mine[0]["name"] == "/other.dll-1a2b3c":
        return error(f"Binary {wanted} not found. Available binaries: {[p['name'] for p in project['programs']]}")
    if name == "search_strings":
        return fast({"strings": [{"value": s, "address": f"{0x9000 + i:08x}"} for i, s in enumerate(STRINGS)
                                 if str(args.get("query") or "") in s]})
    if name == "search_symbols_by_name":
        return fast({"symbols": [{"name": names[a], "address": addr(a), "type": "Function", "namespace": "Global",
                                  "source": "DEFAULT" if names[a].startswith("FUN_") else "USER_DEFINED",
                                  "refcount": 1, "external": False, "is_thunk": False, "thunk_target": None}
                                 for a in sorted(GRAPH)]})
    if name == "decompile_function":
        target = str(args.get("name_or_address"))
        a = find(target)
        if a not in GRAPH:
            return fast([{"name": target, "code": "", "signature": None, "error": f"Function or symbol '{target}' not found.",
                          "callees": None, "referenced_strings": None, "xrefs": None}])
        return fast([{"name": f"{names[a]}-{addr(a)}", "code": code(a), "signature": f"int {names[a]}(int param_1);",
                      "error": None, "callees": [code_name(c) for c in GRAPH[a]] if args.get("include_callees") else None,
                      "referenced_strings": None, "xrefs": None}])
    if name == "rename_function":
        a = find(args.get("name_or_address"))
        old, names[a] = names[a], args["new_name"]
        return fast({"binary_name": args["binary_name"], "address": addr(a), "old_name": old,
                     "new_name": args["new_name"]})
    if name == "set_comment":
        a = find(args.get("target"))
        comments[a] = args["comment"]
        return fast({"binary_name": args["binary_name"], "address": addr(a), "comment": args["comment"],
                     "comment_type": args.get("comment_type")})
    return error("not here")


for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-re", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": pyghidra_tools() if MODE == "pyghidra" else rea_tools()}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"name": name, "args": args}) + "\n")
        if name in FAIL:
            result = error(f"{name} failed on purpose")
        else:
            result = pyghidra_call(name, args) if MODE == "pyghidra" else rea_call(name, args)
    else:
        result = None
    out = ({"jsonrpc": "2.0", "id": rid, "result": result} if result is not None else
           {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}})
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
