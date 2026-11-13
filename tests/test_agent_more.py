"""`mafdet agent`, what makes it last: the shell that stays open and its jobs, notes, the to-do list
and compaction, the web tools, MCP schemas cut to a budget, a resumed session on the screen, and
the options that stay from one start to the next."""
import argparse
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mafdet.agent import mcp, web
from mafdet.agent.loop import Agent
from mafdet.agent.context import STUB
from mafdet.agent.memory import SUMMARIZE, Memory, memory_tools, summarize, tail_from, transcript
from mafdet.agent.model import Reply
from mafdet.agent.permissions import Permissions
from mafdet.agent.shell import Shell
from mafdet.agent.store import Session
from mafdet.agent.tools import builtin_tools
from test_agent import Script, Streams, call, shown, tui_on, until

WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------- the shell

@pytest.fixture
def shell(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    sh = Shell(work, tmp_path / "scratch", threading.Event())
    yield sh
    sh.close()


def test_the_shell_keeps_its_folder_and_variables(shell):
    (shell.workdir / "sub").mkdir()
    out = shell.run("$x = 41; cd sub" if WINDOWS else "x=41; cd sub")
    assert out.startswith("[no output]") and "[now in " in out and out.rstrip("]").endswith("sub")
    assert shell.run("$x + 1" if WINDOWS else "echo $((x + 1))") == "42"          # no "[now in": it did not move
    assert Path(shell.run("(Get-Location).Path" if WINDOWS else "pwd")).name == "sub"
    several = "if ($true) {\n  'one'\n  'due è'\n}" if WINDOWS else "if true; then\n  echo one\n  echo 'due è'\nfi"
    assert shell.run(several) == "one\ndue è"
    failed = shell.run("cmd /c 'exit 3'" if WINDOWS else "sh -c 'exit 3'")
    assert failed == "[no output]\n[exit code 3]"
    assert shell.run("'again'" if WINDOWS else "echo again") == "again"             # the code does not stick


def test_a_command_past_its_timeout_takes_the_shell_and_the_next_one_says_so(shell):
    shell.run("$x = 1" if WINDOWS else "x=1")
    t0 = time.monotonic()
    out = shell.run("'before'; Start-Sleep 30" if WINDOWS else "echo before; sleep 30", timeout=1)
    assert out.startswith("before\nerror: still running after 1 s") and "background" in out
    assert time.monotonic() - t0 < 10
    out = shell.run("\"x=$x.\"" if WINDOWS else "echo \"x=$x.\"")
    assert out.startswith("[a new shell") and out.endswith("x=.")                   # the variable is gone
    assert shell.run("'ok'" if WINDOWS else "echo ok") == "ok"                      # said once

    threading.Timer(0.5, shell.stop.set).start()
    out = shell.run("Start-Sleep 30" if WINDOWS else "sleep 30")
    assert out == "error: stopped by the user"


def test_background_jobs(shell):
    assert shell.job() == "no job started"
    loop = ("1..200 | % { \"tick $_\"; Start-Sleep -Milliseconds 100 }" if WINDOWS
            else "i=0; while [ $i -lt 200 ]; do i=$((i+1)); echo tick $i; sleep 0.1; done")
    out = shell.start_job(loop)
    assert out.startswith("job 1 started in") and "job 1: running" in out
    time.sleep(1.5)
    assert "tick 3" in shell.job(1)
    assert shell.job().startswith("job 1: running")
    stopped = shell.job(1, "stop")
    assert "job 1: ended" in stopped and "tick 1" in stopped
    assert shell.job(7).startswith("error: no job 7 (there: 1)")
    quick = shell.start_job("'done at once'" if WINDOWS else "echo done at once")
    time.sleep(1.0)
    assert "done at once" in shell.job(2) and "ended, exit code 0" in shell.job(2)


def test_the_tools_use_the_session_shell(tmp_path):
    tools = {t.name: t for t in builtin_tools(tmp_path)}
    assert {"shell", "job"} <= set(tools) and tools["job"].risk == "read" and tools["shell"].risk == "exec"
    tools["shell"].run({"command": "$v = 'kept'" if WINDOWS else "v=kept"})
    assert tools["shell"].run({"command": "$v" if WINDOWS else "echo $v"}) == "kept"
    assert tools["job"].run({}) == "no job started"
    started = tools["shell"].run({"command": "'bg'" if WINDOWS else "echo bg", "background": True})
    assert started.startswith("job 1 started")


# ---------------------------------------------------------------- memory

def test_notes_and_todo(tmp_path):
    memory = Memory()
    changes = []
    memory.changed = lambda: changes.append(1)
    memory.clear(tmp_path / "notes.md")
    tools = {t.name: t for t in memory_tools(memory)}
    assert tools["note"].run({"text": " main is at\n0x401000 "}) == "noted (1 notes)"
    assert tools["note"].run({"text": "  "}).startswith("error")
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "- main is at 0x401000\n"
    listed = tools["todo"].run({"items": [{"task": "find main", "done": True}, {"task": "decode the key"}, "run it"]})
    assert listed == "[x] find main\n[ ] decode the key\n[ ] run it"
    assert "Notes written during the work:\n- main is at 0x401000" in memory.text() and "To-do list:" in memory.text()
    assert tools["todo"].run({"items": []}) == "the list is empty"
    assert len(changes) >= 4
    assert all(t.risk == "read" for t in tools.values())


def long_history(n=6):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find the key in check.exe"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"n": i})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"output {i} " + "x" * 1500})
    return msgs


def test_compaction_keeps_the_summary_the_notes_and_the_last_turns(tmp_path):
    msgs = long_history()
    assert tail_from(msgs) == 10                                         # the last two calls, with their results
    assert tail_from(msgs[:4]) == 2 and tail_from(msgs[:2]) == 2         # fewer turns than asked: what there is
    text = transcript(msgs[1:10], 100_000)
    assert text.startswith("USER: find the key") and 'AGENT CALLS read_file({"n": 0})' in text
    assert "RESULT: output 0 x" in text and len(text) < 4000             # outputs cut short
    assert transcript(msgs[1:10], 300).startswith("[the start of the session is not shown]")
    model = Script(Reply(content="Task: find the key. Established: main reads DATA."))
    assert summarize(msgs[1:10], model, 12000, 1 / 3).startswith("Task: find the key.")
    asked, tools = model.seen[0]
    assert tools is None and asked[0] == {"role": "system", "content": SUMMARIZE}
    request = asked[1]["content"]                                        # the transcript, then the instruction again
    assert "<transcript>\nUSER: find the key" in request and request.rstrip().endswith("in your own words.")
    assert "do not repeat them, and do not say whether there are any" in SUMMARIZE
    assert summarize(msgs[1:2], Script(), 12000, 1 / 3) == ""            # no turn of the agent: nothing to ask
    assert summarize(msgs[1:10], Script(Reply(content="")), 12000, 1 / 3) is None

    session = Session()
    paths = {}
    for i in range(6):                                                   # the whole outputs, on disk as the loop keeps them
        paths[f"c{i}"] = session.save_output("read_file", msgs[2 * i + 3]["content"], f"c{i}")
    agent = Agent(Script(Reply(content="Task: find the key. Established: main reads DATA.")),
                  mcp.Toolbox(builtin_tools(tmp_path)), Permissions("ask"), session, system="sys", ctx=12000,
                  max_tokens=1000, tool_chars=1000)
    agent.memory.add_note("the key is XORed with 0x5A")
    agent.memory.set_todo([{"task": "decode", "done": False}])
    agent.messages = [dict(m) for m in msgs]
    agent.paths = dict(paths)
    assert agent.compact()
    new = agent.messages
    assert [m["role"] for m in new] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert new[0] == msgs[0] and new[2] == msgs[10] and new[4] == msgs[12]   # the model's own calls stay
    assert new[3]["content"] == STUB + f"; the whole of it is in {paths['c4']}: read_file it (offset, limit) to get it back]"
    assert new[5]["content"].startswith("output 5 xxx") and paths["c5"] in new[5]["content"]   # the most recent: a head
    assert len(new[5]["content"].split("\n[... the rest")[0]) <= 500      # at most half of --tool-chars
    summary = new[1]["content"]
    assert "Task: find the key. Established: main reads DATA." in summary and "the key is XORed with 0x5A" in summary
    assert "[ ] decode" in summary and "find the key in check.exe" in summary


def test_the_agent_compacts_when_leaving_outputs_out_is_not_enough(tmp_path):
    model = Script(Reply(content="Summary: looking for the key."), Reply(content="found it"))
    events = []
    session = Session()
    agent = Agent(model, mcp.Toolbox(builtin_tools(tmp_path)), Permissions("ask"), session, system="sys",
                  ctx=3000, max_tokens=1000, emit=lambda kind, **d: events.append((kind, d)))
    agent.memory.add_note("DATA is at 0x404000")
    agent.messages += long_history(3)[1:]
    for i in range(30):                                                  # many turns of plain talk: nothing to leave out
        agent.messages.append({"role": "user", "content": f"question {i} " + "y" * 200})
        agent.messages.append({"role": "assistant", "content": f"answer {i} " + "z" * 200})
    assert agent.run("and now?") == "found it"
    kinds = [k for k, _ in events]
    assert "compacted" in kinds and "error" not in kinds
    d = dict(events)["compacted"]
    assert d["after"] < d["before"]
    assert len(agent.messages) < 10 and "Summary: looking for the key." in agent.messages[1]["content"]
    assert "DATA is at 0x404000" in agent.messages[1]["content"]
    assert [m["content"] for m in agent.messages[-2:]] == ["and now?", "found it"]     # the last turns, as they were
    again = Session(session.dir)                                         # a resume starts from the compacted state
    assert [m["role"] for m in again.messages()] == [m["role"] for m in agent.messages[1:]]
    assert again.notes() == ["DATA is at 0x404000"]
    resumed = Agent(Script(), mcp.Toolbox(), Permissions("ask"), again, system="sys", history=again.messages())
    assert resumed.memory.notes == ["DATA is at 0x404000"]


# ---------------------------------------------------------------- the web

PAGE = """<html><head><title>Frida  docs</title><style>p {color: red}</style><script>var x = "<p>no</p>";</script></head>
<body><nav><a href="/home">Home</a></nav><h1>Interceptor</h1><p>Attach to a <b>function</b> &amp; log it.</p>
<ul><li>onEnter</li><li>onLeave</li></ul><pre>Interceptor.attach(ptr, {});</pre><a href="#top">top</a></body></html>"""

DDG = """<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffrida.re%2Fdocs%2F&amp;rut=1">Frida <b>docs</b></a>
<a class="result__snippet" href="x">Dynamic <b>instrumentation</b> toolkit.</a></div>
<div class="result"><a class="result__a" href="https://example.org/a">Second</a><a class="result__snippet">Another one.</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">An ad</a></div>"""


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body, kind, status = PAGE.encode(), "text/html; charset=utf-8", 200
        if self.path.startswith("/bin"):
            body, kind = b"\x00\x01\x02", "application/octet-stream"
        elif self.path.startswith("/missing"):
            body, status = b"nope", 404
        elif self.path.startswith("/searx"):
            body, kind = json.dumps({"results": [{"title": "T", "url": "https://t.example", "content": "C"}]}).encode(), "application/json"
            Site.asked = self.path
        elif self.path.startswith("/ddg"):
            body = DDG.encode()
        elif self.path.startswith("/plain"):
            body, kind = "testo è".encode(), "text/plain; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_a_page_as_text(site):
    out = web.fetch(site + "/doc")
    assert out.startswith(f"Frida docs\n{site}/doc\n\n")
    assert "# Interceptor\nAttach to a function & log it." in out
    assert "- onEnter\n- onLeave" in out and "Interceptor.attach(ptr, {});" in out
    assert f"Home ({site}/home)" in out and "color: red" not in out and "var x" not in out and "(#top)" not in out
    assert web.fetch(site + "/plain") == f"{site}/plain\n\ntesto è"
    assert web.fetch(site + "/bin").startswith("error: not text (application/octet-stream")
    assert web.fetch(site + "/missing") == "error: the server answered 404 Not Found"
    assert web.fetch("file:///etc/passwd") == "error: only http and https addresses"
    assert web.fetch("http://127.0.0.1:9/").startswith("error: cannot reach it")


def test_search_results(site, monkeypatch):
    assert web._duckduckgo(DDG) == [
        {"title": "Frida docs", "url": "https://frida.re/docs/", "snippet": "Dynamic instrumentation toolkit."},
        {"title": "Second", "url": "https://example.org/a", "snippet": "Another one."}]
    monkeypatch.setattr(web, "DUCKDUCKGO", site + "/ddg?q={query}")
    assert web.search("frida docs") == ("Frida docs\nhttps://frida.re/docs/\nDynamic instrumentation toolkit.\n\n"
                                        "Second\nhttps://example.org/a\nAnother one.")
    assert web.search("x y", site + "/searx?q={query}&format=json") == "T\nhttps://t.example\nC"
    assert Site.asked == "/searx?q=x+y&format=json"
    assert web.search("  ") == "error: an empty query"
    monkeypatch.setattr(web, "DUCKDUCKGO", site + "/doc?q={query}")
    assert web.search("nothing").startswith("no result")
    tools = {t.name: t for t in web.web_tools()}
    assert set(tools) == {"web_search", "web_fetch"} and all(t.risk == "read" for t in tools.values())


def test_the_web_is_off_unless_asked_for(tmp_path):
    from mafdet import agent
    args = argparse.Namespace(tools="", profile="", mcp=[], web=False, search_url="", no_install=True)
    box, _, _ = agent.build_toolbox(args, tmp_path, memory=Memory())
    assert set(box.active) == {"read_file", "write_file", "edit_file", "search", "shell", "job", "note", "todo"}
    assert len(json.dumps(box.specs())) < 3600                           # eight tools: about 1,100 tokens
    args.no_install = False                                              # need_tool by default: ~250 tokens more
    box, _, _ = agent.build_toolbox(args, tmp_path, memory=Memory())
    assert "need_tool" in box.active and len(json.dumps(box.active["need_tool"].spec())) < 1200
    args.no_install = True
    args.web = True
    box, _, _ = agent.build_toolbox(args, tmp_path, memory=Memory())
    assert {"web_search", "web_fetch"} <= set(box.active)


# ---------------------------------------------------------------- MCP schemas on a budget

def test_a_heavy_schema_is_cut_to_the_budget():
    spec = {"name": "decompile", "description": "Decompile one analyzed procedure. " * 12,
            "inputSchema": {"type": "object", "required": ["address"], "properties": {
                "address": {"type": "string", "description": "The procedure's address or symbol. " * 6},
                "provider": {"anyOf": [{"type": "string", "enum": ["ghidra", "hopper", "rizin"]}, {"type": "null"}],
                             "description": "Which backend answers. " * 8, "default": None},
                "options": {"type": "object", "description": "Tuning. " * 20, "properties": {
                    "depth": {"type": "integer", "description": "How deep. " * 10},
                    "format": {"type": "string", "enum": [f"f{i}" for i in range(20)]}}},
                "ranges": {"type": "array", "description": "Ranges. " * 10,
                           "items": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}},
                **{f"flag_{i}": {"type": "boolean", "description": f"Whether to do thing {i}. " * 5} for i in range(6)}}}}
    server = argparse.Namespace(name="fake")
    tool = mcp.as_tool(server, spec)
    props = tool.parameters["properties"]
    # Ten parameters: bare names and types are already above the budget, and nothing a call needs is dropped:
    # the required parameter keeps a few words, the description whole sentences.
    assert len(json.dumps(spec)) > 2800 and len(tool.description) + len(json.dumps(tool.parameters)) < 620
    assert tool.description == "Decompile one analyzed procedure. " * 2 + "Decompile one analyzed procedure."
    assert props["address"]["description"] == "The procedure's address or symbol."
    few = {"name": "f", "description": spec["description"], "inputSchema": {
        "type": "object", "required": ["address"],
        "properties": {k: v for k, v in spec["inputSchema"]["properties"].items() if not k.startswith("flag")}}}
    cut = mcp.as_tool(server, few)
    assert len(cut.description) + len(json.dumps(cut.parameters)) <= mcp.TOOL_CHARS
    assert "description" in cut.parameters["properties"]["address"]      # the required one keeps its words
    assert set(props) =={"address", "provider", "options", "ranges"} | {f"flag_{i}" for i in range(6)}
    assert tool.parameters["required"] == ["address"] and props["address"]["type"] == "string"
    assert props["provider"]["type"] == "string" and props["provider"]["enum"] == ["ghidra", "hopper", "rizin"]
    assert props["options"] == {"type": "object"} or set(props["options"]) <= {"type", "description"}
    assert props["ranges"]["items"] == {"type": "object"} and props["flag_0"]["type"] == "boolean"

    light = {"name": "xrefs", "description": "References to an address.",
             "inputSchema": {"type": "object", "properties": {"address": {"type": "string", "description": "hex"}}}}
    assert mcp.as_tool(server, light).parameters["properties"]["address"] == {"type": "string", "description": "hex"}


def test_descriptions_keep_whole_sentences_or_the_first_clause():
    clip = mcp._clip
    long = ("Decompile one analyzed procedure by symbol name or address. Ghidra also accepts file-offset:0x... "
            "selectors and falls back to a source mapping. Hopper may return null.")
    first = "Decompile one analyzed procedure by symbol name or address."
    assert clip(long, 100) == first
    assert clip(long, 150) == first + " Ghidra also accepts file-offset:0x... selectors and falls back to a source mapping."
    assert clip("The active target's provider document name; use open_binary to switch targets", 48) == \
        "The active target's provider document name"
    assert clip("A rather long sentence without any stop that goes on and on and on", 30) == "A rather long sentence…"
    assert clip("Short; fine", 48) == "Short; fine"


def test_rea_like_schemas_are_cheap_and_still_complete():
    """Sixteen tools shaped like a reverse-engineering server's (long descriptions full of per-backend
    caveats, the same optional `document` everywhere): the active ones cost what a 16k context can pay,
    and every parameter keeps name, type, required and enum."""
    caveat = ("Ghidra terminal-call assumptions that may cut the analyzed body are reported in Evidence "
              "limitations; Hopper preserves observed names. ")
    document = {"description": "The active target's provider document name; use open_binary to switch targets",
                "type": "string"}
    specs = [{"name": f"procedure_{i}", "annotations": {"readOnlyHint": False},
              "description": f"Return thing {i} for one analyzed procedure identified by symbol or address. " + caveat * 3,
              "inputSchema": {"type": "object", "$schema": "https://json-schema.org/draft/2020-12/schema",
                              "additionalProperties": False, "examples": [{"procedure": "main"}],
                              "required": ["procedure"],
                              "properties": {"procedure": {"type": "string", "description": "The procedure name or address"},
                                             "mode": {"default": "literal", "type": "string", "enum": ["literal", "regex"]},
                                             "length": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                                             "document": document}}} for i in range(16)]
    specs += [{**specs[0], "name": "late"}, {**specs[0], "name": "other"}]
    server = argparse.Namespace(name="rea", list_tools=lambda: specs)
    box = mcp.Toolbox()
    box.add_server(server, ["procedure_*"])
    sent = box.specs()
    assert [s["function"]["name"] for s in sent][16:] == ["find_tools"]
    size = len(json.dumps(sent[:16], ensure_ascii=False))
    assert size < 16 * 500 and len(json.dumps(specs[:16])) > 2.4 * size           # ~16 x 110 tokens
    first, later = sent[0]["function"], sent[1]["function"]
    assert first["description"] == "Return thing 0 for one analyzed procedure identified by symbol or address."
    params = later["parameters"]
    assert params["required"] == ["procedure"] and set(params["properties"]) == {"procedure", "mode", "length", "document"}
    assert params["properties"]["procedure"]["description"] == "The procedure name or address"   # required: always said
    assert params["properties"]["mode"]["enum"] == ["literal", "regex"]
    assert params["properties"]["length"] == {"type": "integer"}                     # the server checks bounds
    # The same optional parameter, said once: the first tool describes it, the others give name and type.
    assert first["parameters"]["properties"]["document"]["description"] == "The active target's provider document name"
    assert params["properties"]["document"] == {"type": "string"}
    assert box.get("procedure_1").parameters["properties"]["document"]["description"]   # the tool itself keeps it
    # A tool turned on later goes at the end: what was sent before stays the same, the prompt's start too.
    box.find({"enable": ["late"]})
    again = box.specs()
    assert again[:16] == sent[:16] and [s["function"]["name"] for s in again][16:] == ["find_tools", "late"]
    assert "description" not in again[-1]["function"]["parameters"]["properties"]["document"]
    assert "required" not in again[16]["function"]["parameters"]                      # find_tools: no empty list


# ---------------------------------------------------------------- the interface

def test_tui_shows_a_resumed_session_and_the_todo_list(tmp_path):
    from mafdet.agent.tui import AgentTUI
    session = Session()
    for m in [{"role": "system", "content": "sys"}, {"role": "user", "content": "what is in a.txt?"},
              {"role": "assistant", "content": "", "tool_calls": [
                  {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
              {"role": "tool", "tool_call_id": "c1", "content": "1\thello"},
              {"role": "assistant", "content": "It says **hello**."}]:
        session.log(m)
    model = Streams(Reply(tool_calls=[call("todo", {"items": [{"task": "read it", "done": True}, {"task": "explain it"}]}),
                                      call("note", {"text": "a.txt says hello"}, "c3")]),
                    Reply(content="done"))
    memory = Memory()
    box = mcp.Toolbox(builtin_tools(tmp_path) + memory_tools(memory))

    def make(emit, ask, mode="ask", fresh=False):
        s = Session() if fresh else session
        return Agent(model, box, Permissions(mode, ask), s, system="sys", emit=emit, memory=memory,
                     history=None if fresh else s.messages())

    async def go():
        app = AgentTUI(make, label="fake-model", workdir=str(tmp_path))
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.pause(0.3)
            text = shown(app)
            assert "(resumed, 4 messages)" in text and "> what is in a.txt?" in text
            assert "● read_file  path=a.txt" in text and "⎿ 1\thello" in text and "It says **hello**." in text
            assert text.index("It says **hello**.") < text.index("resumed here")
            assert not app.query_one("#todo").display
            await pilot.press(*"go", "space", *"on", "enter")
            await until(pilot, lambda: not app.running and len(model.seen) == 2)
            await pilot.pause(0.3)
            panel = app.query_one("#todo")
            assert panel.display and "To do  1/2" in str(panel.render()) and "[ ] explain it" in str(panel.render())
            await pilot.press(*"/notes", "enter")
            await pilot.pause(0.2)
            assert "- a.txt says hello" in shown(app)
            await pilot.press(*"/new", "enter")
            await pilot.pause(0.3)
            assert not app.query_one("#todo").display and "what is in a.txt?" not in shown(app)
    asyncio.run(go())


def test_tui_compact_command(tmp_path):
    long = " and so on" * 150                   # long enough that a summary makes it smaller
    model = Streams(Reply(content="one" + long), Reply(content="two" + long), Reply(content="three"),
                    Reply(content="The user asked three things."))

    async def go():
        app = tui_on(tmp_path, model)
        async with app.run_test(size=(110, 40)) as pilot:
            for word in ("a", "b", "c"):
                await pilot.press(word, "enter")
                await until(pilot, lambda: not app.running)
            await pilot.press(*"/compact", "enter")
            await until(pilot, lambda: not app.running and len(model.seen) == 4)
            await pilot.pause(0.2)
            assert "was summarized" in shown(app)
            roles = [m["role"] for m in app.agent.messages]
            assert roles == ["system", "user", "assistant", "user", "assistant"]
            assert "The user asked three things." in app.agent.messages[1]["content"]
    asyncio.run(go())


# ---------------------------------------------------------------- options that stay

def test_saved_options(mafdet_home):
    from mafdet import agent
    from mafdet.cli import build_parser
    parse = build_parser().parse_args
    args = parse(["agent"])
    assert args.model is None and args.approve is None and args.web is None
    agent.settings(args)
    assert (args.model, args.approve, args.mcp, args.profile, args.tools, args.web) == ("", "ask", [], "", "", False)

    args = parse(["agent", "--model", "qwen3-14b", "--mcp", "rea", "--profile", "native-static", "--web",
                  "--approve", "edits", "--save"])
    agent.settings(args)
    saved = json.loads((mafdet_home / "agent.json").read_text(encoding="utf-8"))
    assert saved == {"provider": "", "model": "qwen3-14b", "approve": "edits", "mcp": ["rea"], "profile": "native-static",
                     "tools": "", "web": True}

    args = parse(["agent"])                                              # the next start, with nothing said
    agent.settings(args)
    assert (args.model, args.approve, args.mcp, args.profile, args.web) == ("qwen3-14b", "edits", ["rea"],
                                                                            "native-static", True)
    args = parse(["agent", "--no-web", "--approve", "ask"])              # the command line wins, and saves nothing
    agent.settings(args)
    assert args.web is False and args.approve == "ask" and args.model == "qwen3-14b"
    assert json.loads((mafdet_home / "agent.json").read_text(encoding="utf-8"))["web"] is True
