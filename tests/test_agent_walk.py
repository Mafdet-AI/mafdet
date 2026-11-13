"""`mafdet agent --walk`: a binary's functions named bottom-up into the book, on a fake decompiler
(`agent_fake_re.py`, rea-shaped and pyghidra-mcp-shaped) and a scripted model."""
import io
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from mafdet.agent import backend, book as book_module, mcp
from mafdet.agent.book import Book, book_tool
from mafdet.agent.model import Reply, Stopped
from mafdet.agent.permissions import Permissions
from mafdet.agent.walk import (NO_CALL, PROGRAM_SYSTEM, RECORD, RECORD_PROGRAM, REVISE_SYSTEM, WALK_SYSTEM, Walk,
                               bottom_up, check, check_program)

FAKE_RE = str(Path(__file__).with_name("agent_fake_re.py"))
ANALYSED = 13                    # the fake graph's functions, less printf (an import)


def call(name, args, id="c1"):
    return {"id": id, "name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}


def good(addr, name=None):
    n = backend.addr_int(addr)
    return Reply(tool_calls=[call("record_function", {"name": name or f"fn_{n:x}", "summary": f"does {n:#x}",
                                                      "confidence": "high"})])


PROGRAM = "A small command-line tool that adds numbers and prints the result, for a test."


def draft(messages):
    """The draft of a revise prompt: {"name", "summary", "confidence"}."""
    text = messages[1]["content"].split("The first reading's draft", 1)[1]
    return dict(re.findall(r"^- (name|summary|confidence): (.*)$", text, re.M))


def keep(messages, **change):
    """A second-pass answer: the draft as it is, or with `change`."""
    d = {**draft(messages), **change}
    return Reply(tool_calls=[call("record_function", d)])


class Namer:
    """A model naming each function fn_<address>, unless `decide(address, attempt)` says otherwise. In the
    second pass it keeps every draft, unless `revise(address, attempt, messages)` says otherwise; it sums
    the program up as PROGRAM, unless `program(attempt)` says otherwise."""

    def __init__(self, decide=None, revise=None, program=None):
        self.decide, self.revise, self.program = decide, revise, program
        self.seen = []                      # (messages, tools) of every request
        self.order = []                     # the address of every request ("program" for the program's)
        self.kw = []                        # the other arguments of every request
        self.revised = []                   # the address of every second-pass request

    def chat(self, messages, tools=None, should_stop=None, **kw):
        self.seen.append((json.loads(json.dumps(messages)), json.loads(json.dumps(tools))))
        self.kw.append(dict(kw))
        attempt = sum(1 for m in messages if m["role"] == "assistant") + 1
        if tools[0]["function"]["name"] == "record_program":
            self.order.append("program")
            reply = (self.program(attempt) if self.program else None) or Reply(
                tool_calls=[call("record_program", {"summary": PROGRAM})])
        else:
            addr = re.search(r"^Function \S+ at (\S+?)(?: \(\d+ bytes\))?[:.]$", messages[1]["content"], re.M).group(1)
            self.order.append(addr)
            if messages[0]["content"] == REVISE_SYSTEM:
                self.revised.append(addr)
                reply = (self.revise(addr, attempt, messages) if self.revise else None) or keep(messages)
            else:
                reply = (self.decide(addr, attempt) if self.decide else None) or good(addr)
        reply.prompt_tokens, reply.completion_tokens = 100, 20
        return reply

    def prompt(self, addr):
        """The first pass's prompt of a function."""
        return next(m[1]["content"] for (m, _), a in zip(self.seen, self.order) if a == addr)

    def revise_prompt(self, addr):
        return next(m[1]["content"] for (m, _), a in zip(self.seen, self.order)
                    if a == addr and m[0]["content"] == REVISE_SYSTEM)


def make_binary(tmp_path, name="target.exe"):
    binary = tmp_path / name
    if not binary.exists():
        binary.write_bytes(b"MZ\x90\x00 a fake program for the walk")
    return binary


def walk_on(tmp_path, model, mode="rea", env=None, binary=None, **kw):
    binary = binary or make_binary(tmp_path)
    server = mcp.McpServer("fake-re", sys.executable, [FAKE_RE, mode], env=env)
    out = io.StringIO()
    try:
        kw.setdefault("ctx", 8192)
        kw.setdefault("max_tokens", 1024)
        kw.setdefault("poll", 0.05)
        w = Walk(Book.for_binary(binary), server, model, out=out, think=False, **kw)
        result = w.run(binary)
        if w.adapter is not None:                                             # as the command does
            w.adapter.close()
    finally:
        server.close()
    return w, result, out.getvalue()


def test_bottom_up_order_and_what_each_prompt_says(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model)
    fs = w.book.functions
    order = [hex(backend.addr_int(a)) for a in model.order]
    assert len(order) == ANALYSED and "EXTERNAL:0x1" not in model.order      # the import is not sent
    assert fs["EXTERNAL:0x1"]["status"] == "known" and fs["EXTERNAL:0x1"]["orig"] == "MSVCRT.DLL::printf"
    assert fs["0x3300"]["callees"] == ["EXTERNAL:0x1"] and fs["0x3300"]["outside"] == []
    pos = {a: i for i, a in enumerate(order)}
    for a, f in fs.items():                                                   # callees first, except in cycles
        for c in f["callees"] or []:
            if c in pos and a in pos and {a, c} != {"0x6000", "0x6100"} and a != c:
                assert pos[c] < pos[a], (c, a)
    assert order[-2:] == ["0x1000", "0x4000"]                                 # the roots, by address
    assert pos["0x2200"] < pos["0x2100"] < pos["0x2000"]
    assert pos["0x3300"] < min(pos["0x3100"], pos["0x3200"]) and max(pos["0x3100"], pos["0x3200"]) < pos["0x3000"]
    assert pos["0x6000"] < pos["0x6100"]                                      # a cycle: by address

    root = model.prompt("0x1000")
    assert "fn_2000 — does 0x2000" in root and "fn_9000 — does 0x9000" in root
    assert "MSVCRT.DLL::printf — imported" in root and "printf(param_1)" in root
    assert "fn_2000(param_1)" in root                                         # the code says the new names too
    assert not re.search(r"FUN_0000(2000|3000|6000|8000|9000)", root)
    assert "FUN_00001000" in root                                             # its own name, for the model to replace
    assert "\r" not in root                                                   # Ghidra's CRLF, read as lines
    assert "fn_3300 — does 0x3300" in model.prompt("0x3100")
    assert "FUN_00006100 — not named yet" in model.prompt("0x6000")           # inside a cycle, the peer as it was
    assert "fn_6000 — does 0x6000" in model.prompt("0x6100") and "fn_6000(param_1)" in model.prompt("0x6100")
    assert "this function itself" in model.prompt("0x8000")
    assert "It calls no other function" in model.prompt("0x4000")
    huge = model.prompt("0x9000")
    assert "characters left out" in huge and len(huge) < 20000 and "filler line 1999" in huge   # head and tail

    systems = {json.dumps(m[0]) for m, _ in model.seen}
    tools = {json.dumps(t) for _, t in model.seen}
    assert systems == {json.dumps({"role": "system", "content": WALK_SYSTEM})} and tools == {json.dumps([RECORD])}
    assert all(len(m) == 2 for m, _ in model.seen)                            # a fresh conversation each time

    saved = json.loads((w.book.dir / "book.json").read_text(encoding="utf-8"))
    assert saved["functions"]["0x2000"]["name"] == "fn_2000" and saved["functions"]["0x2000"]["callees"] == ["0x2100"]
    assert saved["binary"]["sha256"].startswith(w.book.dir.name) and saved["backend"]["adapter"] == "rea"
    assert saved["functions"]["0x2000"]["prompt_tokens"] == 100 and saved["functions"]["0x2000"]["steps"] == 1
    md = (w.book.dir / "book.md").read_text(encoding="utf-8")
    assert md.index("**fn_1000**") < md.index("**fn_2000**") < md.index("**fn_2100**")   # top-down
    assert "**fn_4000**" in md and "*(above)*" in md
    lines = [line for line in out.splitlines() if line.startswith("[")]
    assert len(lines) == ANALYSED and re.match(r"\[1/13\] 0x\w+ FUN_\w+ -> fn_\w+  \[high\]  \d+\.\d s  100\+20 tok", lines[0])
    assert "13 named, 0 failed" in out and "1 known" in out and not result["stopped"]
    assert "not written back" in out                                          # ask, nobody to ask: no mirror


def test_a_real_name_with_code_is_part_of_the_graph_and_the_top_of_the_book(tmp_path):
    """where.exe (2026-11-12): `entry` is a real name, so it was never asked for its callees; the book's tree
    then began at the unnamed function with the lowest address, a CRT initializer, not at entry -> main."""
    log = tmp_path / "calls.jsonl"
    model = Namer()
    w, result, out = walk_on(tmp_path, model, env={"FAKE_RE_LOG": str(log), "FAKE_RE_ENTRY": "1"})
    fs = w.book.functions
    assert fs["0x500"]["status"] == "known" and fs["0x500"]["callees"] == ["0x1000"] and "0x500" not in model.order
    assert fs["EXTERNAL:0x1"]["callees"] is None                              # an import: nothing to ask
    asked = [c["args"]["procedure"] for c in calls_logged(log) if c["name"] == "procedure_callees"]
    assert "0x500" in asked and "EXTERNAL:0x1" not in asked
    assert w.book.roots() == ["0x500", "0x4000"]                              # the most below it first
    md = (w.book.dir / "book.md").read_text(encoding="utf-8")
    tree = md.split("## From the roots down", 1)[1]
    first = [line for line in tree.splitlines() if line.startswith("- ")][0]
    assert first.startswith("- entry `0x500`") and "named in the binary" in first
    assert tree.index("entry `0x500`") < tree.index("  - **fn_1000**") < tree.index("- **fn_4000**")
    assert "  - calls: printf" in tree and "MSVCRT.DLL::printf `" not in tree    # imports on one line
    kids = [line.split("**")[1] for line in tree.splitlines() if line.startswith("    - **")]
    assert kids[:3] == ["fn_3000", "fn_2000", "fn_6000"]                      # under fn_1000: the most below first
    assert "    - calls: **fn_3000**, **fn_2000**, **fn_6000**, **fn_9000**, **fn_8000**, printf\n" in tree


def test_the_same_through_json_in_text_and_callees_as_names(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model, mode="rea-text")
    assert w.book.counts()["done"] == ANALYSED and w.book.functions["0x6100"]["callees"] == ["0x6000", "0x2200"]
    assert w.book.functions["0x3300"]["callees"] == ["EXTERNAL:0x1"]          # MSVCRT.DLL::printf, by name
    assert "fn_2000 — does 0x2000" in model.prompt("0x1000")


def test_bad_answers_are_told_back_once_then_the_function_fails_and_the_walk_goes_on(tmp_path):
    def decide(addr, attempt):
        if addr == "0x2100":
            return good(addr, "2bad name!")
        if addr == "0x3100" and attempt == 1:
            return Reply(content="It adds things.")
        if addr == "0x3200" and attempt == 1:
            return Reply(tool_calls=[call("record_function", "{nope")])
        if addr == "0x4000" and attempt == 1:
            return Reply(tool_calls=[call("record_function", {"name": "sub_4000", "summary": "x"})])
    model = Namer(decide)
    w, result, out = walk_on(tmp_path, model)
    fs = w.book.functions
    assert fs["0x2100"]["status"] == "failed" and "not a C identifier" in fs["0x2100"]["reason"]
    assert fs["0x2100"]["steps"] == 2 and model.order.count("0x2100") == 2
    assert fs["0x3100"]["status"] == "done" and fs["0x3100"]["steps"] == 2
    retry = [m for m, _ in model.seen if m[1]["content"].startswith("Function FUN_00003100") and len(m) > 2][0]
    assert retry[-1] == {"role": "user", "content": NO_CALL}
    retry = [m for m, _ in model.seen if m[1]["content"].startswith("Function FUN_00003200") and len(m) > 2][0]
    assert retry[-1]["role"] == "tool" and "not a JSON object" in retry[-1]["content"]
    assert fs["0x4000"]["name"] == "fn_4000"                                  # a default name is not a name
    assert fs["0x1000"]["status"] == "done" and w.book.counts()["done"] == ANALYSED - 1
    assert "0x2100 FUN_00002100 failed: name '2bad name!' is not a C identifier" in out
    assert "FUN_00002100 — not named yet" in model.prompt("0x2000")

    again = Namer()                                                           # failed ones wait for --retry-failed
    walk_on(tmp_path, again)
    assert again.order == []
    again = Namer()
    w, _, _ = walk_on(tmp_path, again, retry_failed=True)
    assert again.order == ["0x2100"] and w.book.functions["0x2100"]["name"] == "fn_2100"


def test_a_stop_mid_run_then_a_rerun_goes_on_without_redoing(tmp_path):
    stop = threading.Event()

    class StopsAtFour(Namer):
        def chat(self, messages, tools=None, should_stop=None, **kw):
            if len(self.seen) == 3:
                on_disk = json.loads(next(tmp_path.rglob("book.json")).read_text(encoding="utf-8"))["runs"][-1]
                assert on_disk["done"] == 3 and on_disk["ended"]              # what a kill here would leave
                stop.set()                                                    # Esc, Ctrl+C: the stream is closed
                assert should_stop()
                raise Stopped
            return super().chat(messages, tools, should_stop=should_stop, **kw)

    first = StopsAtFour()
    w, result, out = walk_on(tmp_path, first, stop=stop)
    assert result["stopped"] == "stopped" and w.book.counts()["done"] == 3
    saved = json.loads((w.book.dir / "book.json").read_text(encoding="utf-8"))
    assert sum(f["status"] == "done" for f in saved["functions"].values()) == 3
    assert not list(w.book.dir.glob("*.tmp"))

    second = Namer()
    w, result, out = walk_on(tmp_path, second)
    assert len(second.order) == ANALYSED - 3 and not set(second.order) & set(first.order)
    assert w.book.counts()["done"] == ANALYSED and len(w.book.data["runs"]) == 2
    assert "functions 14 (0 new)" in out and "3 done before" in out


def test_partial_runs(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model, max_functions=2)
    assert len(model.order) == 2 and w.book.counts()["pending"] == ANALYSED - 2

    class Slow(Namer):
        def chat(self, *a, **kw):
            import time
            time.sleep(1.2)
            return super().chat(*a, **kw)

    model = Slow()
    w, result, out = walk_on(tmp_path, model, time_limit=1.0)
    assert len(model.order) == 1 and result["stopped"] == "time limit" and "time limit reached" in out


def test_the_book_is_written_atomically(tmp_path, monkeypatch):
    checked = []
    real_replace = book_module.os.replace

    def replace(src, dst):
        json.loads(Path(src).read_text(encoding="utf-8")) if str(dst).endswith(".json") else None
        if Path(dst).exists() and str(dst).endswith(".json"):
            json.loads(Path(dst).read_text(encoding="utf-8"))
        checked.append(dst)
        real_replace(src, dst)

    monkeypatch.setattr(book_module.os, "replace", replace)
    w, _, _ = walk_on(tmp_path, Namer())
    assert len([p for p in checked if str(p).endswith("book.json")]) > ANALYSED     # after every function
    before = (w.book.dir / "book.json").read_text(encoding="utf-8")

    def killed(fd):
        raise OSError("killed while writing")
    monkeypatch.setattr(book_module.os, "fsync", killed)
    w.book.functions["0x2000"]["summary"] = "changed"
    with pytest.raises(OSError):
        w.book.save()
    assert (w.book.dir / "book.json").read_text(encoding="utf-8") == before        # the old book, whole


def test_names_are_unique_in_the_book(tmp_path):
    model = Namer(lambda addr, attempt: good(addr, "printf" if addr == "0x3300" else "parse_input"))
    w, _, _ = walk_on(tmp_path, model)
    names = [f["name"] for f in w.book.functions.values() if f["status"] == "done"]
    assert len(set(names)) == ANALYSED and "parse_input" in names and "parse_input_12" in names
    assert w.book.functions["0x3300"]["name"] == "printf_2"     # the import keeps its name (MSVCRT.DLL::printf)


def calls_logged(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_the_mirror_writes_only_what_is_allowed_and_survives_its_failures(tmp_path):
    log = tmp_path / "calls.jsonl"
    w, _, out = walk_on(tmp_path, Namer(), env={"FAKE_RE_LOG": str(log)})               # ask, nobody: nothing
    assert not {"set_address_name", "set_comment"} & {c["name"] for c in calls_logged(log)}
    assert "set_address_name would ask" in out and w.book.functions["0x2000"]["mirror"] == {}

    log.unlink()
    for f in w.book.functions.values():                                       # the same binary from the start
        f.update(status="pending" if f["status"] == "done" else f["status"])
    w.book.save()
    w, _, out = walk_on(tmp_path, Namer(), env={"FAKE_RE_LOG": str(log)}, allow=["set_address_name"])
    names = [c for c in calls_logged(log) if c["name"] == "set_address_name"]
    assert len(names) == ANALYSED and {"address": "0x2000", "name": "fn_2000"} in [c["args"] for c in names]
    assert not [c for c in calls_logged(log) if c["name"] == "set_comment"]
    assert w.book.functions["0x2000"]["mirror"] == {"rename": "written"}

    log.unlink()
    for f in w.book.functions.values():
        f.update(status="pending" if f["status"] == "done" else f["status"], mirror={})
    w.book.save()
    w, result, out = walk_on(tmp_path, Namer(), env={"FAKE_RE_LOG": str(log), "FAKE_RE_FAIL": "set_comment"},
                             permissions=Permissions("all"))
    comments = [c for c in calls_logged(log) if c["name"] == "set_comment"]
    assert len(comments) == ANALYSED and comments[0]["args"]["comment"].startswith("does 0x")
    m = w.book.functions["0x2000"]["mirror"]
    assert m["rename"] == "written" and m["comment"].startswith("failed:") and "on purpose" in m["comment"]
    assert w.book.counts()["done"] == ANALYSED and not result["stopped"] and "(comment failed)" in out


def test_writes_the_host_cannot_run_are_not_tried(tmp_path):
    """rea on Windows lists set_address_name and set_comment but says (binary_session) it cannot run them."""
    log = tmp_path / "calls.jsonl"
    w, result, out = walk_on(tmp_path, Namer(), env={"FAKE_RE_LOG": str(log), "FAKE_RE_NO_WRITE": "1"},
                             permissions=Permissions("all"))
    called = {c["name"] for c in calls_logged(log)}
    assert "binary_session" in called and not {"set_address_name", "set_comment"} & called
    assert "rename: no (unsupported_host — Windows Ghidra P0 does not admit database mutation.)" in out
    assert "not written back: set_address_name: unsupported_host" in out
    assert w.book.counts()["done"] == ANALYSED and w.book.functions["0x2000"]["mirror"] == {}
    assert not w.adapter.has("rename") and w.adapter.has("code")


def test_a_pyghidra_server_is_read_through_its_own_table(tmp_path):
    import hashlib
    log, project = tmp_path / "calls.jsonl", tmp_path / "project.json"
    binary = make_binary(tmp_path)
    program = f"/target.exe-{hashlib.sha1(binary.read_bytes()).hexdigest()[:6]}"
    model = Namer()
    env = {"FAKE_RE_LOG": str(log), "FAKE_RE_PROJECT": str(project)}
    w, result, out = walk_on(tmp_path, model, mode="pyghidra", env=env, permissions=Permissions("all"))
    assert w.adapter.name == "pyghidra-mcp" and "read as pyghidra-mcp" in out and not result["stopped"]
    assert "waiting for target.exe-" in out and "waiting for the analysis to complete" in out   # the import, in the background
    fs = w.book.functions
    assert "00001000" in fs and len(fs) == ANALYSED and w.book.counts()["done"] == ANALYSED   # imports not listed
    assert fs["00006100"]["callees"] == ["00006000", "00002200"]
    assert fs["00003300"]["callees"] == [] and fs["00003300"]["outside"] == ["printf"]
    assert "fn_2000 — does 0x2000" in model.prompt("00001000")
    assert "It also calls (imports, or not resolved): printf" in model.prompt("00003300")
    calls = calls_logged(log)
    imports = [c["args"] for c in calls if c["name"] == "import_binary"]
    assert len(imports) == 1 and imports[0]["binary_path"].endswith("target.exe")
    decompiles = [c["args"] for c in calls if c["name"] == "decompile_function"]
    assert decompiles and all(a["binary_name"] == program for a in decompiles)
    renames = [c["args"] for c in calls if c["name"] == "rename_function"]
    assert {"binary_name": program, "name_or_address": "00002000", "new_name": "fn_2000"} in renames
    assert any(c["name"] == "set_comment" and c["args"]["comment_type"] == "plate" for c in calls)
    assert [c["name"] for c in calls].count("save") == 1 and calls[-1]["name"] == "save"   # written, then saved

    log.unlink()                                                              # the next run: in the project already
    w, result, out = walk_on(tmp_path, Namer(), mode="pyghidra", env=env, retry_failed=True)
    calls = calls_logged(log)
    assert "is in the project already, not imported again" in out and not result["stopped"]
    assert "import_binary" not in {c["name"] for c in calls} and "save" not in {c["name"] for c in calls}


def test_a_server_the_walk_cannot_read(tmp_path):
    server = mcp.McpServer("fake", sys.executable, [str(Path(__file__).with_name("agent_fake_mcp.py")), "2"])
    try:
        binary = make_binary(tmp_path)
        out = io.StringIO()
        result = Walk(Book.for_binary(binary), server, Namer(), out=out).run(binary)
    finally:
        server.close()
    assert result["stopped"].startswith("backend: the server fake has none of the tool sets")


# ---------------------------------------------------------------- the second pass (--revise)

# The first pass's requests on the fake graph (rea), one retry of each kind included, as sha256 of
# json.dumps([messages, tools, other arguments], sort_keys=True)[:16]; taken before the second pass was
# written. The benchmark's baseline is the first pass: a change here changes what it measures.
PASS_ONE = [("0x2200", 2, "efaf01e4db0146da"), ("0x2100", 2, "dc8e4d8a527be091"), ("0x2000", 2, "b66084dc3a43f264"),
            ("0x3300", 2, "4fbbdc766562dbf7"), ("0x3100", 2, "01943df32f127cf8"), ("0x3100", 4, "b86564a2dd8966e8"),
            ("0x3200", 2, "fcac4b26ec516e9b"), ("0x3200", 4, "8a0059671f047e4c"), ("0x3000", 2, "43aedb22d0304fa4"),
            ("0x6000", 2, "997deddcb447201d"), ("0x6100", 2, "92e849dd252c560a"), ("0x8000", 2, "ac7e99e17351a883"),
            ("0x9000", 2, "26218986ee50802a"), ("0x1000", 2, "4c5c01db5dd1d68a"), ("0x4000", 2, "36e88b4152823c65")]


def requests(model, which=None):
    import hashlib
    return [(a, len(m), hashlib.sha256(json.dumps([m, t, kw], sort_keys=True, ensure_ascii=False).encode())
             .hexdigest()[:16]) for (m, t), kw, a in zip(model.seen, model.kw, model.order)
            if which is None or m[0]["content"] == which]


def first_pass_retries(addr, attempt):
    if addr == "0x3100" and attempt == 1:
        return Reply(content="It adds things.")
    if addr == "0x3200" and attempt == 1:
        return Reply(tool_calls=[call("record_function", "{nope")])


def test_the_first_pass_sends_byte_identical_requests(tmp_path):
    model = Namer(first_pass_retries)
    walk_on(tmp_path, model)
    assert requests(model) == PASS_ONE
    model = Namer(first_pass_retries)                     # the same with --revise, on a fresh book
    w, _, _ = walk_on(tmp_path, model, binary=other_binary(tmp_path, "b.exe"), revise=True)
    assert requests(model, WALK_SYSTEM) == PASS_ONE and w.book.data["program"]["status"] == "done"


def test_the_second_pass_goes_from_the_roots_down_with_the_program_and_the_callers(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model, revise=True)
    fs = w.book.functions
    assert not result["stopped"] and result["revise"] == {"depth": None, "skip_high": False, "program": True,
                                                          "callers": True}
    first = len([s for s in model.seen if s[0][0]["content"] == WALK_SYSTEM])
    assert first == ANALYSED and model.order[first] == "program"           # the first pass, the program, the second
    order = model.revised
    assert sorted(order) == sorted(a for a in fs if fs[a]["status"] == "done") and len(order) == ANALYSED
    pos = {a: i for i, a in enumerate(order)}
    for a, f in fs.items():                                                 # callers first, except in cycles
        for c in f["callees"] or []:
            if c in pos and a in pos and {a, c} != {"0x6000", "0x6100"} and a != c:
                assert pos[a] < pos[c], (a, c)
    assert order[:2] == ["0x4000", "0x1000"] and pos["0x6000"] < pos["0x6100"]

    (asked, tools), = [s for s in model.seen if s[1] == [RECORD_PROGRAM]]
    assert asked[0] == {"role": "system", "content": PROGRAM_SYSTEM} and len(asked) == 2
    facts = asked[1]["content"]
    assert facts.startswith("File: target.exe (") and \
        "Functions: 14 (13 named by the first reading, 1 with names of their own: imports, symbols)" in facts
    assert "Imports, by library:\n- MSVCRT.DLL (1): printf" in facts
    assert "- fn_1000 — does 0x1000\n  - fn_3000 — does 0x3000\n    - fn_3100" in facts       # the tree, most below first
    assert "- fn_4000 — does 0x4000" in facts
    strings = facts.split("Some of its strings:\n", 1)[1]
    assert strings.splitlines()[:2] == ['"Usage: target [/s] pattern..."', '"Adds the numbers it is given."']
    assert '"ok"' not in strings and '"%d' not in strings                   # too short to say anything
    assert facts.endswith("Call record_program for this program.")
    assert w.book.data["program"]["summary"] == PROGRAM and w.book.data["program"]["inputs"]["strings"] == 3

    revise = [m for m, t in model.seen if m[0]["content"] == REVISE_SYSTEM]
    assert {json.dumps(t) for m, t in model.seen if m[0]["content"] == REVISE_SYSTEM} == {json.dumps([RECORD])}
    assert all(len(m) == 2 for m in revise) and len(revise) == ANALYSED
    assert all(m[1]["content"].startswith(f"What the program is (from a first look at all of it): {PROGRAM}\n\n")
               for m in revise)
    p = model.revise_prompt("0x2000")
    assert "Function FUN_00002000 at 0x2000.\n\nIt is called by:\n- fn_1000 — does 0x1000\n\nIts pseudo-code:" in p
    assert "int fn_2000(int param_1)" in p and "fn_2100(param_1)" in p     # the current names in the code
    assert "It calls:\n- fn_2100 — does 0x2100" in p
    assert p.endswith("The first reading's draft, made seeing only the functions it calls:\n- name: fn_2000\n"
                      "- summary: does 0x2000\n- confidence: high\n\nCall record_function for this function: keep "
                      "the draft's name if it is right, change it only if it is wrong or misleading.")
    assert "Nothing in the program calls it directly" in model.revise_prompt("0x1000")
    assert "It is called by:\n- fn_2100 — does 0x2100\n- fn_6100 — does 0x6100\n- fn_9000 — does 0x9000" \
        in model.revise_prompt("0x2200")

    rev = fs["0x2000"]["revision"]
    assert rev["status"] == "done" and rev["renamed"] is False and rev["mirror"] == {} and rev["steps"] == 1
    assert rev["with"] == {"program": True, "callers": True} and rev["prompt_tokens"] == 100
    assert fs["0x2000"]["first"] == {"name": "fn_2000", "summary": "does 0x2000", "comment": "", "confidence": "high"}
    assert "fn_2000 kept  [high]" in out and "second pass  13 functions, from the roots down (0 reviewed before)" in out
    assert "second pass in this run: 13 reviewed (0 renamed), 0 failed" in out and "program summary done" in out
    md = (w.book.dir / "book.md").read_text(encoding="utf-8")
    assert f"## The program\n\n{PROGRAM}\n\n## From the roots down" in md
    assert "- second pass (from the roots down): 13 of 13 reviewed, 0 renamed" in md and "→ **" not in md

    again = Namer()                                                        # all done: nothing to do
    w, result, out = walk_on(tmp_path, again, revise=True)
    assert again.order == [] and "second pass  0 functions" in out


def renaming(addr, attempt, messages):
    return {"0x1000": keep(messages, name="add_numbers_main", summary="the program's main"),
            "0x3000": keep(messages, name="read_config"),           # its own name, the suffix taken off: kept
            "0x2000": keep(messages, name="parse_arguments"),        # frees read_config
            "0x2200": keep(messages, name="read_config"),            # takes it
            "0x6000": keep(messages, name="walk_tree")}.get(addr)


def first_names(addr, attempt):
    return good(addr, "read_config") if addr in ("0x2000", "0x3000") else None


def test_a_rename_flows_down_frees_its_name_and_is_mirrored(tmp_path):
    log = tmp_path / "calls.jsonl"
    model = Namer(first_names, renaming)
    w, result, out = walk_on(tmp_path, model, revise=True, env={"FAKE_RE_LOG": str(log)},
                             permissions=Permissions("all"))
    fs = w.book.functions
    assert fs["0x3000"]["first"]["name"] == "read_config_2" and fs["0x3000"]["name"] == "read_config_2"
    assert fs["0x3000"]["revision"]["renamed"] is False
    assert fs["0x2000"]["name"] == "parse_arguments" and fs["0x2000"]["revision"]["renamed"] is True
    assert fs["0x2200"]["name"] == "read_config" and fs["0x2200"]["first"]["name"] == "fn_2200"
    assert "It is called by:\n- add_numbers_main — the program's main" in model.revise_prompt("0x2000")
    assert "It is called by:\n- parse_arguments — does 0x2000" in model.revise_prompt("0x2100")
    p = model.revise_prompt("0x6100")                                      # a cycle: the peer renamed before it
    assert "walk_tree(param_1)" in p and "- walk_tree — does 0x6000" in p and "fn_6000" not in p
    assert w.book.names(1)["0x2000"] == "read_config" and w.book.names(2)["0x2000"] == "parse_arguments"
    assert w.book.names(1)["0x4000"] == w.book.names(2)["0x4000"] == "fn_4000"
    assert w.book.resolve("parse_arguments") == "0x2000" and w.book.resolve("read_config") == "0x2200"

    calls = calls_logged(log)
    renames = [c["args"] for c in calls if c["name"] == "set_address_name"]
    comments = [c["args"] for c in calls if c["name"] == "set_comment"]
    assert [r["name"] for r in renames if r["address"] == "0x1000"] == ["fn_1000", "add_numbers_main"]
    assert [r["name"] for r in renames if r["address"] == "0x3000"] == ["read_config_2"]     # kept: not again
    assert len([c for c in comments if c["address"] == "0x3000"]) == 1
    assert [c["comment"] for c in comments if c["address"] == "0x1000"] == ["does 0x1000", "the program's main"]
    assert [r["name"] for r in renames if r["address"] == "0x2000"] == ["read_config", "parse_arguments"]
    assert len(renames) == ANALYSED + 4 and len(comments) == ANALYSED + 1
    assert fs["0x1000"]["revision"]["mirror"] == {"rename": "written", "comment": "written"}
    assert fs["0x2000"]["revision"]["mirror"] == {"rename": "written"} and fs["0x3000"]["revision"]["mirror"] == {}
    assert "0x1000 fn_1000 -> add_numbers_main  [high]" in out and "read_config_2 kept" in out
    assert "13 reviewed (4 renamed)" in out

    md = (w.book.dir / "book.md").read_text(encoding="utf-8")
    assert "- fn_1000 → **add_numbers_main** `0x1000` [high] — the program's main" in md
    assert "fn_2200 → **read_config**" in md and "| read_config → parse_arguments | FUN_00002000 |" in md
    assert "13 of 13 reviewed, 4 renamed" in md
    assert w.book.lookup(address="0x2000").startswith("0x2000 parse_arguments (was FUN_00002000; first named read_config)")

    # The decompiler still saying a first name (a rename it was not given, or that failed): the new name,
    # unless the old one is someone else's now (read_config is 0x2200's).
    assert w.revise_code(fs["0x1000"], "a = read_config(1); b = fn_6000(2); c = fn_60001(3);") == \
        "a = read_config(1); b = walk_tree(2); c = fn_60001(3);"


def test_a_stop_in_the_second_pass_then_a_rerun_goes_on_without_redoing(tmp_path):
    stop = threading.Event()

    class StopsAtThird(Namer):
        def chat(self, messages, tools=None, should_stop=None, **kw):
            if len(self.revised) == 2 and messages[0]["content"] == REVISE_SYSTEM:
                book = json.loads(next(tmp_path.rglob("book.json")).read_text(encoding="utf-8"))
                assert sum("revision" in f for f in book["functions"].values()) == 2    # what a kill would leave
                assert book["program"]["status"] == "done"
                stop.set()
                raise Stopped
            return super().chat(messages, tools, should_stop=should_stop, **kw)

    first = StopsAtThird()
    w, result, out = walk_on(tmp_path, first, stop=stop, revise=True)
    assert result["stopped"] == "stopped" and first.revised == ["0x4000", "0x1000"]
    assert sum("revision" in f for f in w.book.functions.values()) == 2

    second = Namer()
    w, result, out = walk_on(tmp_path, second, revise=True)
    assert "program" not in second.order and not [a for a, (m, _) in zip(second.order, second.seen)
                                                  if m[0]["content"] == WALK_SYSTEM]
    assert len(second.revised) == ANALYSED - 2 and not set(second.revised) & {"0x4000", "0x1000"}
    assert "(2 reviewed before)" in out and not result["stopped"]
    assert all(f["revision"]["status"] == "done" for f in w.book.functions.values() if f["status"] == "done")


def other_binary(tmp_path, name):
    binary = tmp_path / name
    binary.write_bytes(b"MZ\x90\x00 a fake program for the walk, " + name.encode())
    return binary


def test_the_second_pass_scope(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model, revise_depth=1, revise=True)
    assert sorted(model.revised) == ["0x1000", "0x2000", "0x3000", "0x4000", "0x6000", "0x8000", "0x9000"]
    assert "within 1 calls of a root" in out and w.book.depths()["0x3300"] == 3
    model = Namer()                                                        # wider: only what is left
    walk_on(tmp_path, model, revise=True)
    assert sorted(model.revised) == ["0x2100", "0x2200", "0x3100", "0x3200", "0x3300", "0x6100"]

    def medium(addr, attempt):
        return Reply(tool_calls=[call("record_function", {"name": "unsure_one", "summary": "maybe",
                                                          "confidence": "medium"})]) if addr == "0x2000" else None
    model = Namer(medium)
    walk_on(tmp_path, model, binary=other_binary(tmp_path, "b.exe"), revise=True, revise_skip_high=True)
    assert model.revised == ["0x2000"]

    model = Namer()                                                        # --max-functions over both passes
    w, result, out = walk_on(tmp_path, model, binary=other_binary(tmp_path, "c.exe"), revise=True,
                             max_functions=ANALYSED + 2)
    assert len(model.revised) == 2 and "2 of 13 in this run (--max-functions)" in out
    model = Namer()
    w, result, out = walk_on(tmp_path, model, binary=other_binary(tmp_path, "d.exe"), revise=True, max_functions=5)
    assert model.revised == [] and "second pass: waits for the first (8 functions still to name" in out
    assert "program" not in model.order


def test_the_second_pass_variants_and_a_program_not_summarized(tmp_path):
    model = Namer()
    w, result, out = walk_on(tmp_path, model, revise=True, revise_program=False)
    assert "program" not in model.order and "program" not in w.book.data and "## The program" not in \
        (w.book.dir / "book.md").read_text(encoding="utf-8")
    p = model.revise_prompt("0x2000")
    assert p.startswith("Function FUN_00002000 at 0x2000.\n\nIt is called by:") and "What the program is" not in p
    assert w.book.functions["0x2000"]["revision"]["with"] == {"program": False, "callers": True}
    assert {m[0]["content"] for m, _ in model.seen if "Its pseudo-code" in m[1]["content"]} == {REVISE_SYSTEM}

    model = Namer()
    w, result, out = walk_on(tmp_path, model, binary=other_binary(tmp_path, "b.exe"), revise=True,
                             revise_callers=False)
    p = model.revise_prompt("0x2000")
    assert "It is called by" not in p and "Nothing in the program calls it" not in p and PROGRAM in p
    assert result["revise"] == {"depth": None, "skip_high": False, "program": True, "callers": False}

    model = Namer(program=lambda attempt: Reply(content="It is a program."))
    w, result, out = walk_on(tmp_path, model, binary=other_binary(tmp_path, "c.exe"), revise=True)
    assert model.order.count("program") == 2 and w.book.data["program"]["status"] == "failed"
    assert "program  not summarized: no record_program call (the second pass goes on without it)" in out
    assert "What the program is" not in model.revise_prompt("0x2000") and len(model.revised) == ANALYSED
    retry = [m for m, t in model.seen if t == [RECORD_PROGRAM]][1]
    assert retry[-1]["content"].startswith("You did not call record_program")


def test_a_book_from_before_the_second_pass_loads_and_revises(tmp_path):
    w, _, _ = walk_on(tmp_path, Namer())
    path = w.book.dir / "book.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for f in data["functions"].values():                                   # as the first pass wrote it before
        for k in ("first", "revision"):
            assert k not in f
        f.pop("first", None)
    data.pop("program", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    model = Namer(revise=lambda addr, attempt, messages: keep(messages, name="entry_point_of_it")
                  if addr == "0x1000" else None)
    w, result, out = walk_on(tmp_path, model, revise=True)
    assert not result["stopped"] and len(model.revised) == ANALYSED
    f = w.book.functions["0x1000"]
    assert f["first"]["name"] == "fn_1000" and f["name"] == "entry_point_of_it" and f["revision"]["renamed"]


def test_the_second_pass_through_pyghidra(tmp_path):
    log, project = tmp_path / "calls.jsonl", tmp_path / "project.json"
    model = Namer(revise=lambda addr, attempt, messages: keep(messages, name="add_numbers_main")
                  if addr == "00001000" else None)
    env = {"FAKE_RE_LOG": str(log), "FAKE_RE_PROJECT": str(project)}
    w, result, out = walk_on(tmp_path, model, mode="pyghidra", env=env, permissions=Permissions("all"), revise=True)
    assert not result["stopped"] and len(model.revised) == ANALYSED
    facts = [m for m, t in model.seen if t == [RECORD_PROGRAM]][0][1]["content"]
    assert "- (library not given) (1): printf" in facts and '"Usage: target [/s] pattern..."' in facts
    calls = calls_logged(log)
    renames = [c["args"]["new_name"] for c in calls if c["name"] == "rename_function"
               and c["args"]["name_or_address"] == "00001000"]
    assert renames == ["fn_1000", "add_numbers_main"] and calls[-1]["name"] == "save"
    assert "strings (search_strings)" in out


def test_the_program_summary_is_checked():
    assert check_program(call("record_program", {"summary": "  A tool\n that  lists files in a folder. "})) == \
        ({"summary": "A tool that lists files in a folder."}, "")
    assert "too short" in check_program(call("record_program", {"summary": "A tool."}))[1]
    assert "missing" in check_program(call("record_program", {}))[1]
    assert "only tool is record_program" in check_program(call("record_function", {"summary": "x"}))[1]
    long = check_program(call("record_program", {"summary": "It reads files. " * 100}))[0]["summary"]
    assert len(long) <= 1200 and long.endswith("files.")


def test_a_rename_frees_the_name_in_the_book(tmp_path):
    book = Book(tmp_path / "b")
    book.merge([{"address": "0x10", "name": "FUN_10"}, {"address": "0x20", "name": "FUN_20"}], lambda n: False)
    book.record("0x10", "parse", "s", "", "high")
    book.record("0x20", "parse", "s", "", "high")
    assert book.functions["0x20"]["name"] == "parse_2"
    assert book.revise("0x20", "parse", "s2", "", "high") is False and book.functions["0x20"]["name"] == "parse_2"
    assert book.revise("0x10", "read", "s", "", "high") is True
    assert book.revise("0x20", "parse_header", "s", "", "high") is True and book.unique("parse", "0x30") == "parse"
    assert book.functions["0x20"]["first"]["name"] == "parse_2"                # the first answer, kept once
    assert book.names(1) == {"0x10": "parse", "0x20": "parse_2"} and book.names(2) == {"0x10": "read",
                                                                                     "0x20": "parse_header"}


# ---------------------------------------------------------------- the pieces

def test_tarjan_leaves_first_and_deep_graphs():
    edges = {"a": ["b", "c"], "b": ["d"], "c": ["d", "c"], "d": [], "e": ["f"], "f": ["e", "d"]}
    comps = bottom_up(sorted(edges), edges)
    assert comps.index(["d"]) < comps.index(["b"]) < comps.index(["a"]) and ["e", "f"] in comps
    assert comps.index(["c"]) < comps.index(["a"]) and comps.index(["d"]) < comps.index(["e", "f"])
    n = 50000                                                                  # deeper than any recursion limit
    chain = {str(i): [str(i + 1)] for i in range(n)} | {str(n): ["0"]}
    assert len(bottom_up(list(chain), chain)) == 1
    chain[str(n)] = []
    assert [c[0] for c in bottom_up(list(chain), chain)][:2] == [str(n), str(n - 1)]


def test_results_are_read_whatever_their_shape():
    env = {"evidence_id": "ev_x", "normalized_result": [{"address": "0x10", "value": "main"}]}
    assert backend.payload({"structuredContent": env, "content": [{"type": "text", "text": "ok"}]}) == env["normalized_result"]
    assert backend.payload({"content": [{"type": "text", "text": json.dumps(env)}]}) == env["normalized_result"]
    assert backend.payload({"structuredContent": {"result": ["a", "b"]}}) == ["a", "b"]
    assert backend.payload({"content": [{"type": "text", "text": "plain words"}]}) == "plain words"
    assert backend.functions("0x401000 sub_401000\nmain @ 0x401100\n00401200: FUN_00401200") == [
        {"address": "0x401000", "name": "sub_401000", "external": False, "thunk": False, "source": "", "size": None},
        {"address": "0x401100", "name": "main", "external": False, "thunk": False, "source": "", "size": None},
        {"address": "00401200", "name": "FUN_00401200", "external": False, "thunk": False, "source": "", "size": None}]
    assert backend.functions({"0x10": "a"})[0]["name"] == "a"
    assert backend.functions([{"name": "sub_20"}])[0]["address"] == "0x20"
    assert backend.refs([{"address": 16, "name": "x"}, "printf", ""]) == ["0x10", "printf"]
    assert backend.text([{"code": "int f();"}]) == "int f();" and backend.text(None) == ""
    assert backend.text("\r\nint f()\r\n{\r\n}\r\n") == "\nint f()\n{\n}\n"
    rea = {"address": "EXTERNAL:0x13", "value": "MSVCRT.DLL::memset",                 # rea 6.9.0, as listed
           "symbol": {"external": True, "type": "function", "source": "imported"},
           "procedure": {"external": True, "thunk": False, "thunk_target": None}}
    assert backend.functions([rea]) == [{"address": "EXTERNAL:0x13", "name": "MSVCRT.DLL::memset", "external": True,
                                         "thunk": False, "source": "imported", "size": None}]
    assert backend.addr_int("EXTERNAL:0x13") is None and backend.addr_int("140004a04") == 0x140004a04
    with pytest.raises(backend.BackendError, match="not found"):
        backend.dig([{"name": "x", "code": "", "error": "not found"}], [0, "code"])
    assert backend.dig({"code": "c"}, [0, "code"]) == "c"
    assert [backend.is_default(n) for n in ("sub_401a30", "FUN_00401000", "fcn.00401000", "thunk_FUN_0040",
                                            "printf", "main", "sub_main", "thunk_printf")] == [True] * 4 + [False] * 4
    assert backend.addr_int("0x401A30") == backend.addr_int("00401a30") == backend.addr_int("ram:00401a30") == 0x401a30
    assert backend.pick(["list_procedures", "procedure_callees", "procedure_pseudo_code"])["name"] == "rea"
    assert backend.pick(["search_symbols_by_name", "decompile_function"])["name"] == "pyghidra-mcp"
    assert backend.pick(["decompile_function"]) is None


def test_what_the_model_records_is_checked():
    ok, why = check(call("record_function", {"name": " `parse_header()` ", "summary": "reads\n  the header",
                                             "confidence": "HIGH", "comment": "magic 0x5A4D"}))
    assert ok == {"name": "parse_header", "summary": "reads the header", "comment": "magic 0x5A4D",
                  "confidence": "high"} and why == ""
    assert check(call("record_function", {"name": "x" * 61, "summary": "s"}))[1].endswith("3 to 60 characters")
    assert "says nothing" in check(call("record_function", {"name": "FUN_0040", "summary": "s"}))[1]
    assert "summary is missing" in check(call("record_function", {"name": "parse_it"}))[1]
    assert "no tool" in check(call("read_file", {}))[1]
    assert check(call("record_function", {"name": "a_b", "summary": "s", "confidence": "sure"}))[0]["confidence"] == "low"


def test_a_session_reads_the_book(tmp_path):
    w, _, _ = walk_on(tmp_path, Namer())
    book = Book.find(str(make_binary(tmp_path)))
    assert book.dir == w.book.dir and Book.find(w.book.dir.name[:8]).dir == w.book.dir
    tool = book_tool(book)
    assert tool.risk == "read" and "target.exe" in tool.description
    one = tool.run({"address": "0x2000"})
    assert one.startswith("0x2000 fn_2000 (was FUN_00002000) — does 0x2000 [high confidence]")
    assert "calls:\n  0x2100 fn_2100" in one and "called by:\n  0x1000 fn_1000" in one
    assert tool.run({"address": "FUN_00002100"}).startswith("0x2100 fn_2100")
    assert tool.run({"query": "does 0x22"}).startswith("0x2200 fn_2200")
    assert "printf" in tool.run({"query": "import"})
    overview = tool.run({})
    assert overview.startswith("target.exe: 14 functions, 13 done, 1 known") and "0x4000 fn_4000" in overview
    assert tool.run({"query": "nothing like this"}).startswith("nothing in the book")
    with pytest.raises(SystemExit):
        Book.find(str(tmp_path / "other.exe"))


# ---------------------------------------------------------------- the command

from test_agent import FakeEngine, delta, engine  # noqa: E402,F401  (engine: the fixture)


def test_the_command_walks_on_an_engine_already_running(tmp_path, mafdet_home, engine):
    from mafdet import agent
    from mafdet.cli import build_parser
    mafdet_home.mkdir(parents=True, exist_ok=True)
    (mafdet_home / "mcp.json").write_text(json.dumps({"mcpServers": {"fake-re": {
        "command": sys.executable, "args": [FAKE_RE, "rea"], "allow": ["set_address_name"]}}}), encoding="utf-8")
    FakeEngine.chunks = [
        delta(tool_calls=[{"index": 0, "id": "r1", "function": {"name": "record_function", "arguments": json.dumps(
            {"name": "helper", "summary": "helps", "confidence": "medium"})}}]),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 30}}]
    binary = make_binary(tmp_path)
    args = build_parser().parse_args(["agent", "--walk", str(binary), "--mcp", "fake-re", "--url", engine,
                                      "--max-functions", "3", "--no-think"])
    assert args.walk and args.max_functions == 3 and args.retry_failed is False and args.time_limit == 0
    assert agent.run(args) == 0
    book = Book.find(str(binary))
    done = sorted(f["name"] for f in book.functions.values() if f["status"] == "done")
    assert done == ["helper", "helper_2", "helper_3"]
    assert all(f["mirror"] == {"rename": "written"} for f in book.functions.values() if f["status"] == "done")
    body = FakeEngine.bodies[0]
    assert body["tools"] == [RECORD] and body["messages"][0]["content"] == WALK_SYSTEM
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["cache_prompt"] is True

    with pytest.raises(SystemExit, match="--mcp"):
        agent.run(build_parser().parse_args(["agent", "--walk", str(binary)]))

    from mafdet.agent.memory import Memory
    import argparse
    ns = argparse.Namespace(tools="", profile="", mcp=[], web=False, search_url="", book=str(binary))
    box, _, _ = agent.build_toolbox(ns, tmp_path, memory=Memory())
    assert "book" in box.active and box.get("book").run({"address": "0x2200"}).startswith("0x2200 helper")
    ns.book = ""
    box, _, _ = agent.build_toolbox(ns, tmp_path, memory=Memory())
    assert "book" not in box.active                                            # no book, no schema


def test_the_walk_starts_its_engine_with_a_small_context(tmp_path, mafdet_home, monkeypatch):
    """Not the agent's largest that fits: at 24k the 14B fell to 5-27 tok/s on the RTX 4070 SUPER when the
    desktop took more VRAM; one function at a time needs far less (2026-11-12)."""
    from mafdet import agent
    from mafdet.agent.walk import WALK_CTX
    from mafdet.cli import build_parser
    mafdet_home.mkdir(parents=True, exist_ok=True)
    (mafdet_home / "mcp.json").write_text(json.dumps({"mcpServers": {"fake-re": {
        "command": sys.executable, "args": [FAKE_RE, "rea"]}}}), encoding="utf-8")
    seen = []

    def start_engine(args):
        seen.append(args.ctx)
        raise SystemExit("no engine in a test")
    monkeypatch.setattr(agent, "start_engine", start_engine)
    binary = make_binary(tmp_path)
    for extra, want in (([], WALK_CTX), (["--ctx", "8192"], 8192)):
        with pytest.raises(SystemExit):
            agent.run(build_parser().parse_args(["agent", "--walk", str(binary), "--mcp", "fake-re"] + extra))
        assert seen[-1] == want
    assert WALK_CTX == 16384


def test_the_revise_options_of_the_command(tmp_path, mafdet_home, monkeypatch, capsys):
    from mafdet import agent
    from mafdet.agent import walk as walk_module
    from mafdet.cli import build_parser
    mafdet_home.mkdir(parents=True, exist_ok=True)
    (mafdet_home / "mcp.json").write_text(json.dumps({"mcpServers": {"fake-re": {
        "command": sys.executable, "args": [FAKE_RE, "rea"]}}}), encoding="utf-8")
    seen = []

    class Fake:
        adapter = None

        def __init__(self, *a, **kw):
            seen.append(kw)

        def run(self, binary):
            return {"stopped": ""}
    monkeypatch.setattr(walk_module, "Walk", Fake)
    binary = make_binary(tmp_path)
    base = ["agent", "--walk", str(binary), "--mcp", "fake-re", "--url", "http://127.0.0.1:9", "--ctx", "8192"]
    for extra, want in (([], (False, None, False, True, True)),
                        (["--revise"], (True, None, False, True, True)),
                        (["--revise-depth", "2"], (True, 2, False, True, True)),
                        (["--revise", "--revise-skip-high", "--revise-no-program", "--revise-no-callers"],
                         (True, None, True, False, False))):
        assert agent.run(build_parser().parse_args(base + extra)) == 0
        kw = seen[-1]
        assert (kw["revise"], kw["revise_depth"], kw["revise_skip_high"], kw["revise_program"],
                kw["revise_callers"]) == want, extra
    with pytest.raises(SystemExit, match="--revise is a second pass of a walk"):
        agent.run(build_parser().parse_args(["agent", "--revise"]))
    with pytest.raises(SystemExit):
        build_parser().parse_args(["agent", "--help"])
    help_text = capsys.readouterr().out
    assert re.search(r"^  --revise-depth N", help_text, re.M)                     # an option's line
    for hidden in ("--revise-skip-high", "--revise-no-program", "--revise-no-callers"):   # not listed as options...
        assert not re.search(rf"^  {hidden}", help_text, re.M)
    sub = next(a for a in build_parser()._subparsers._group_actions[0].choices["agent"]._actions
               if "--revise" in a.option_strings)
    assert all(h in sub.help for h in ("--revise-skip-high", "--revise-no-program", "--revise-no-callers"))  # ...named


def test_an_engine_gone_leaves_functions_pending_and_stops_the_walk(tmp_path):
    from mafdet.agent.model import ModelError

    class Gone(Namer):
        def chat(self, messages, tools=None, **kw):
            super().chat(messages, tools, **kw)
            raise ModelError("the engine at http://127.0.0.1:8079 does not answer: refused")

    w, result, out = walk_on(tmp_path, Gone())
    assert "three times in a row" in result["stopped"] and w.book.counts()["pending"] == ANALYSED

    class TooLong(Namer):
        def chat(self, messages, tools=None, **kw):
            reply = super().chat(messages, tools, **kw)
            if self.order[-1] == "0x2200":
                raise ModelError("the engine answered 400: the request exceeds the available context size")
            return reply

    w, result, out = walk_on(tmp_path, TooLong())
    assert w.book.functions["0x2200"]["status"] == "failed" and "exceeds" in w.book.functions["0x2200"]["reason"]
    assert w.book.counts()["done"] == ANALYSED - 1 and not result["stopped"]
