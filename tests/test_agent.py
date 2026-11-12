"""`mafdet agent`: the loop on a scripted model, the tools, the permissions, the context budget,
the MCP client on a fake server, and the model client on a fake engine."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mafdet.agent import mcp
from mafdet.agent.context import STUB, Context, ContextFull, clip
from mafdet.agent.loop import Agent
from mafdet.agent.model import ChatModel, ModelError, Reply
from mafdet.agent.permissions import Permissions
from mafdet.agent.store import Session
from mafdet.agent.tools import builtin_tools

FAKE_MCP = str(Path(__file__).with_name("agent_fake_mcp.py"))


class Script:
    """A model that says what it is told to, and keeps what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools=None, **kw):
        self.seen.append((json.loads(json.dumps(messages)), tools))
        reply = self.replies.pop(0)
        reply.prompt_tokens = reply.prompt_tokens or 100
        return reply


def call(name, args, id="c1"):
    return {"id": id, "name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}


def agent_on(tmp_path, model, mode="ask", ask=None, **kw):
    box = mcp.Toolbox(builtin_tools(tmp_path))
    events = []
    agent = Agent(model, box, Permissions(mode, ask), Session(), system="sys",
                  emit=lambda kind, **d: events.append((kind, d)), **kw)
    return agent, events


def test_loop_calls_a_tool_and_answers(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    model = Script(Reply(reasoning="let me look", tool_calls=[call("read_file", {"path": "a.txt"})]),
                   Reply(content="It says hello world."))
    agent, events = agent_on(tmp_path, model)
    assert agent.run("what is in a.txt?") == "It says hello world."
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.messages[3]["content"] == "1\thello\n2\tworld"
    assert "reasoning" not in json.dumps(agent.messages)            # past reasoning stays out of the history
    assert model.seen[1][0][3]["tool_call_id"] == "c1"
    assert [m["role"] for m in agent.session.messages()] == roles[1:]


def test_what_the_model_gets_wrong_is_told_back(tmp_path):
    model = Script(Reply(tool_calls=[call("read_file", "{not json", "c1"), call("decompile", {}, "c2"),
                                     call("read_file", {}, "c3"), call("read_file", {"path": "nope.txt"}, "c4")]),
                   Reply(content="done"))
    agent, _ = agent_on(tmp_path, model)
    assert agent.run("go") == "done"
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert "not a JSON object" in results[0]
    assert "no tool named decompile" in results[1] and "read_file" in results[1]
    assert "needs path" in results[2]
    assert results[3].startswith("error: FileNotFoundError")


def test_a_write_asks_and_a_declined_call_does_not_run(tmp_path):
    model = Script(Reply(tool_calls=[call("write_file", {"path": "x.txt", "content": "hi"})]), Reply(content="ok"))
    agent, events = agent_on(tmp_path, model)                        # nobody to ask: declined
    agent.run("write it")
    assert not (tmp_path / "x.txt").exists()
    assert "declined" in agent.messages[3]["content"]
    assert ("declined", {"name": "write_file"}) in events

    asked = []
    model = Script(Reply(tool_calls=[call("write_file", {"path": "x.txt", "content": "hi"})]), Reply(content="ok"))
    agent, _ = agent_on(tmp_path, model, ask=lambda tool, args, why: asked.append(why) or "y")
    agent.run("write it")
    assert (tmp_path / "x.txt").read_text() == "hi" and asked == ["writes a file"]


def test_permissions(tmp_path):
    tools = {t.name: t for t in builtin_tools(tmp_path)}
    outside = str(tmp_path.parent / "elsewhere.txt")
    asked = []

    def ask(tool, args, why):
        asked.append((tool.name, why))
        return "a"

    p = Permissions("ask")
    assert p.allowed(tools["read_file"], {"path": "a.txt"})
    assert not p.allowed(tools["read_file"], {"path": outside})          # outside the folder: asks, nobody there
    assert not p.allowed(tools["shell"], {"command": "dir"})
    p = Permissions("edits")
    assert p.allowed(tools["write_file"], {"path": "a.txt", "content": ""})
    assert not p.allowed(tools["shell"], {"command": "dir"})
    assert not p.allowed(tools["write_file"], {"path": outside, "content": ""})
    p = Permissions("all", ask)
    assert p.allowed(tools["shell"], {"command": "dir"}) and not asked
    assert p.allowed(tools["write_file"], {"path": outside, "content": ""})   # even with `all`, outside asks
    assert asked == [("write_file", "outside the working folder")]
    p = Permissions("ask", ask)
    assert p.allowed(tools["shell"], {"command": "dir"}) and p.allowed(tools["shell"], {"command": "dir"})
    assert len(asked) == 2                                               # "always": asked once for the tool
    assert p.allowed(tools["read_file"], {"path": outside}) and p.allowed(tools["read_file"], {"path": outside})
    assert len(asked) == 4                                               # but outside the folder, every time


def test_file_tools(tmp_path):
    tools = {t.name: t for t in builtin_tools(tmp_path)}
    tools["write_file"].run({"path": "src/a.c", "content": "int main() {\n  return 1;\n}\n"})
    assert tools["edit_file"].run({"path": "src/a.c", "old": "return 1", "new": "return 0"}).startswith("edited")
    assert "0 times" in tools["edit_file"].run({"path": "src/a.c", "old": "return 7", "new": ""})
    assert tools["read_file"].run({"path": "src/a.c", "offset": 2, "limit": 1}) == "2\t  return 0;\n[1 more lines: offset 3]"
    assert tools["search"].run({"pattern": "RETURN", "glob": "*.c"}).replace("\\", "/") == "src/a.c:2: return 0;"
    assert tools["search"].run({"glob": "*.c"}).replace("\\", "/") == "src/a.c"
    (tmp_path / "b.bin").write_bytes(b"MZ\x90\x00" + bytes(range(60)))
    dump = tools["read_file"].run({"path": "b.bin", "limit": 2})
    assert dump.startswith("binary file, 64 bytes") and "00000000  4d 5a 90 00" in dump and dump.count("\n") == 2
    out = tools["shell"].run({"command": "echo mafdet"})
    assert "mafdet" in out


def test_long_outputs_are_cut_and_kept_on_disk(tmp_path):
    (tmp_path / "long.txt").write_text("\n".join(f"line {i}" for i in range(3000)))
    model = Script(Reply(tool_calls=[call("read_file", {"path": "long.txt", "limit": 3000})]), Reply(content="ok"))
    agent, _ = agent_on(tmp_path, model, tool_chars=1000)
    agent.run("read it")
    shown = agent.messages[3]["content"]
    assert len(shown) < 1400 and "characters left out" in shown and "2999" in shown
    saved = Path(agent.paths["c1"])
    assert saved.read_text().endswith("line 2999") and str(saved) in shown
    assert clip("short", 100) == "short"


def test_old_outputs_are_left_out_when_the_context_fills():
    ctx = Context(ctx=3000, reserve=1000, keep_last=2)                   # 2000 tokens, about 6000 characters
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 1500})
    assert ctx.fit(msgs, [], {"c0": "out/0.txt"}) == 4
    outs = [m["content"] for m in msgs if m["role"] == "tool"]
    assert outs[0] == STUB + "; it is in out/0.txt]" and outs[1].startswith(STUB)
    assert outs[4] == outs[5] == "x" * 1500                              # the last two are never touched
    assert ctx.fit(msgs, []) == 0
    msgs.append({"role": "user", "content": "y" * 9000})
    with pytest.raises(ContextFull):
        ctx.fit(msgs, [])
    ctx.calibrate(msgs, [], prompt_tokens=10)                            # the engine's count wins over the guess
    assert ctx.ratio == 0.15


def test_an_interrupted_turn_leaves_a_history_the_engine_accepts(tmp_path):
    class Stops(Script):
        def chat(self, *a, **kw):
            if not self.replies:
                raise KeyboardInterrupt
            return super().chat(*a, **kw)

    def boom(tool, args, why):
        raise KeyboardInterrupt

    model = Stops(Reply(tool_calls=[call("shell", {"command": "echo 1"}, "c1"), call("shell", {"command": "x"}, "c2")]))
    agent, _ = agent_on(tmp_path, model, ask=boom)
    with pytest.raises(KeyboardInterrupt):
        agent.run("go")
    assert [m["tool_call_id"] for m in agent.messages if m["role"] == "tool"] == ["c1", "c2"]
    resumed = Session(agent.session.dir).messages()
    assert [m["role"] for m in resumed] == ["user", "assistant", "tool", "tool"]


def test_a_session_resumes_without_a_half_done_tail(tmp_path):
    s = Session()
    for m in [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
              {"role": "assistant", "content": "a"}, {"role": "user", "content": "u2"},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "c9"}]}]:
        s.log(m)
    assert [m["content"] for m in Session.find("last").messages()] == ["u", "a", "u2"]
    assert Session.find(s.id).dir == s.dir


def test_step_limit(tmp_path):
    model = Script(*[Reply(tool_calls=[call("search", {}, f"c{i}")]) for i in range(3)])
    agent, events = agent_on(tmp_path, model, max_steps=3)
    assert agent.run("loop") == ""
    assert events[-1][0] == "error" and "3 steps" in events[-1][1]["text"]


# ---------------------------------------------------------------- MCP

@pytest.fixture
def server():
    s = mcp.McpServer("fake", sys.executable, [FAKE_MCP, "20"])
    yield s
    s.close()


def test_mcp_client(server):
    assert server.instructions == "Open a binary first."
    names = [t["name"] for t in server.list_tools()]
    assert len(names) == 22 and names[0] == "tool_0"
    assert server.call("tool_0", {"title": "x"}) == 'tool_0 {"title": "x"}\n[image left out: this model reads text only]'
    assert server.call("tool_1", {}) == "error: it broke"


def test_many_tools_stay_hidden_until_found(server):
    box = mcp.Toolbox(builtin_tools(Path(".")))
    box.add_server(server, ["tool_0", "decomp*"])
    assert set(box.active) == {"read_file", "write_file", "edit_file", "search", "shell", "job", "tool_0",
                               "decompile_function", "find_tools"}
    assert len(box.hidden) == 20
    t0 = box.get("tool_0")
    assert t0.risk == "read" and t0.source == "fake" and box.get("decompile_function").risk == "exec"
    assert len(t0.description) <= mcp.DESCRIPTION_CHARS
    props = t0.parameters["properties"]
    assert list(props) == ["title"] and "title" not in props["title"]
    assert len(props["title"]["description"]) <= mcp.PARAMETER_CHARS
    assert "$schema" not in t0.parameters and "additionalProperties" not in t0.parameters
    assert len(json.dumps(box.specs())) < 3700                           # 9 tools: about 1,200 tokens

    found = box.find({"query": "long output"})
    assert found.splitlines()[0].startswith("big:") and "enable" in found
    assert box.find({"enable": ["big", "nope", "tool_0"]}) == ("big: on, call it from the next step\n"
                                                              "nope: no such tool\ntool_0: already on")
    assert box.get("big").run({}).count("line of output") == 1400

    small = mcp.Toolbox()
    small.hidden, small.active = {}, {}
    few = mcp.McpServer("few", sys.executable, [FAKE_MCP, "2"])
    try:
        small.add_server(few)                                            # a small server: all its tools, no finder
        assert set(small.active) == {"tool_0", "tool_1", "decompile_function", "big"}
    finally:
        few.close()


def test_mcp_tools_in_the_loop(tmp_path, server):
    box = mcp.Toolbox(builtin_tools(tmp_path))
    box.add_server(server, ["tool_0", "big"])
    model = Script(Reply(tool_calls=[call("tool_0", {"title": "a"}, "c1"), call("big", {}, "c2")]),
                   Reply(content="ok"))
    agent = Agent(model, box, Permissions("ask"), Session(), system="sys")
    agent.run("go")
    results = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert results[0].startswith('tool_0 {"title": "a"}')               # read-only: runs by itself
    assert "declined" in results[1]                                      # not marked read-only: asks

    trusted = mcp.Toolbox()
    trusted.add_server(server, ["big", "tool_2"], allow=["bi*"])        # the user's `allow` in mcp.json
    assert trusted.get("big").risk == "read" and trusted.get("tool_2").risk == "exec"


def test_mcp_server_that_does_not_start():
    with pytest.raises(mcp.McpError):
        mcp.McpServer("none", "no-such-command-mafdet")


# ---------------------------------------------------------------- the model client

class FakeEngine(BaseHTTPRequestHandler):
    chunks: list = []
    bodies: list = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        if self.status != 200:
            data = json.dumps({"error": {"message": "the request exceeds the available context size"}}).encode()
            self.send_response(self.status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in self.chunks:
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


@pytest.fixture
def engine():
    FakeEngine.bodies, FakeEngine.status = [], 200
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def delta(**d):
    return {"choices": [{"delta": d, "finish_reason": None}]}


def test_model_client_reads_a_streamed_tool_call(engine):
    FakeEngine.chunks = [
        delta(reasoning_content="I should "), delta(reasoning_content="look."), delta(content="Looking"),
        delta(tool_calls=[{"index": 0, "id": "abc", "function": {"name": "read_file", "arguments": '{"pa'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "a.txt"}'}}]),
        delta(tool_calls=[{"index": 1, "function": {"name": "search", "arguments": "{}"}}]),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 321, "completion_tokens": 12}},
    ]
    seen = []
    reply = ChatModel(engine, "m").chat([{"role": "user", "content": "hi"}], [{"type": "function"}], think=False,
                                        on_delta=lambda kind, text: seen.append((kind, text)))
    assert reply.reasoning == "I should look." and reply.content == "Looking" and reply.finish == "tool_calls"
    assert reply.tool_calls == [{"id": "abc", "name": "read_file", "arguments": '{"path": "a.txt"}'},
                                {"id": "call_1", "name": "search", "arguments": "{}"}]
    assert (reply.prompt_tokens, reply.completion_tokens) == (321, 12)
    assert seen == [("think", "I should "), ("think", "look."), ("text", "Looking")]
    body = FakeEngine.bodies[0]
    assert body["cache_prompt"] is True and body["id_slot"] == 0 and body["stream"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert reply.message()["tool_calls"][0]["function"]["name"] == "read_file"

    ChatModel(engine, "m", llama=False).chat([{"role": "user", "content": "hi"}])
    assert "cache_prompt" not in FakeEngine.bodies[1] and "tools" not in FakeEngine.bodies[1]


def test_model_client_errors(engine):
    FakeEngine.status = 400
    with pytest.raises(ModelError, match="exceeds the available context"):
        ChatModel(engine, "m").chat([{"role": "user", "content": "hi"}])
    with pytest.raises(ModelError, match="does not answer"):
        ChatModel("http://127.0.0.1:9", "m", timeout=2).chat([])


def test_cli_knows_the_agent():
    from mafdet.cli import build_parser
    args = build_parser().parse_args(["agent", "--mcp", "rea", "--mcp", "ghidra", "--profile", "native-static",
                                      "-p", "hello", "--approve", "edits"])
    assert args.mcp == ["rea", "ghidra"] and args.think is True and args.ctx == 0


def test_the_context_follows_the_vram(monkeypatch, tmp_path):
    """--ctx 0: the largest context that keeps the model all on the GPU, on the catalog's estimate."""
    from mafdet import agent
    from mafdet.core import catalog
    from test_hardware_catalog import apple_m2_pro, nvidia_pc, reference_pc
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"")

    def as_variant(variant_id):
        v = catalog.find(variant_id)
        monkeypatch.setattr(catalog, "header", lambda path: {"meta": {"general.architecture": "qwen3",
                                                                      "qwen3.context_length": 40960}})
        monkeypatch.setattr(catalog, "splits_with_cpu",
                            lambda path, hw, ctx=8192: catalog.estimate(v, hw, "cuda", ctx)["placement"] != "gpu")

    rtx_5070_ti = nvidia_pc("NVIDIA GeForce RTX 5070 Ti", 16303, 2000, 896)
    as_variant("qwen3-14b:Q4_K_M")
    assert agent.pick_ctx(str(gguf), rtx_5070_ti) == 24576               # 32k would put layers on the CPU
    assert agent.pick_ctx(str(gguf), reference_pc()) == 16384            # split on 6 GB anyway: the floor
    assert agent.pick_ctx(str(gguf), rtx_5070_ti, device="cpu") == 16384
    assert agent.pick_ctx(str(gguf), apple_m2_pro()) == 16384
    as_variant("qwen3-14b:IQ4_XS")
    assert agent.pick_ctx(str(gguf), rtx_5070_ti) == 32768
    as_variant("qwen3-8b:Q4_K_M")
    assert agent.pick_ctx(str(gguf), rtx_5070_ti) == 32768
    monkeypatch.setattr(catalog, "header", lambda path: {"meta": {"general.architecture": "x",
                                                                  "x.context_length": 8192}})
    assert agent.pick_ctx(str(gguf), rtx_5070_ti) == 16384               # trained on less than any of them


# ---------------------------------------------------------------- the terminal interface

class Streams(Script):
    """A scripted model that streams what it says, as the real client does."""

    def chat(self, messages, tools=None, on_delta=None, **kw):
        reply = super().chat(messages, tools)
        if reply.reasoning and on_delta:
            on_delta("think", reply.reasoning)
        if reply.content and on_delta:
            on_delta("text", reply.content)
        reply.completion_tokens = 10
        return reply


def tui_on(tmp_path, model, mode="ask"):
    from mafdet.agent.tui import AgentTUI
    stop = threading.Event()
    box = mcp.Toolbox(builtin_tools(tmp_path, stop=stop))

    def make(emit, ask, mode=mode, fresh=False):
        return Agent(model, box, Permissions(mode, ask), Session(), system="sys", emit=emit, stop=stop)
    return AgentTUI(make, label="fake-model", workdir=str(tmp_path), mode=mode)


async def until(pilot, check, seconds=10.0):
    for _ in range(int(seconds / 0.05)):
        if check():
            return
        await pilot.pause(0.05)
    raise AssertionError("still waiting")


def shown(app) -> str:
    from textual.widgets import Markdown, Static
    out = []
    for w in app.query("#log > *"):
        out.append(w.source if isinstance(w, Markdown) else str(w.render()) if isinstance(w, Static) else "")
    return "\n".join(out)


def test_tui_a_turn_with_a_tool(tmp_path):
    import asyncio
    (tmp_path / "a.txt").write_text("hello\n")

    model = Streams(Reply(reasoning="I will read it.", tool_calls=[call("read_file", {"path": "a.txt"})]),
                    Reply(content="It says **hello**. [not markup]"))

    async def go():
        app = tui_on(tmp_path, model)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.press(*"what is in a.txt?")
            await pilot.press("shift+enter")                 # a second line, not a send
            await pilot.press(*"ok")
            assert not app.running
            await pilot.press("enter")
            await until(pilot, lambda: not app.running and len(model.seen) == 2)
            await pilot.pause(0.2)
            text = shown(app)
            assert "> what is in a.txt?\nok" in text
            assert "∴ thought for" in text and "I will read it." not in text     # folded
            assert "● read_file  path=a.txt" in text and "⎿ 1\thello" in text
            assert "It says **hello**. [not markup]" in text                     # the answer, as Markdown
            assert app.used == 110
            await pilot.press("ctrl+o")
            await pilot.pause(0.1)
            assert "I will read it." in shown(app)
            await pilot.press("up")                          # the earlier message comes back
            assert app.query_one("#prompt").text == "what is in a.txt?\nok"
            await pilot.press("ctrl+c")                      # clears it
            assert app.query_one("#prompt").text == ""
            await pilot.press("shift+tab")
            assert app.mode == "edits" and app.agent.permissions.mode == "edits"
            await pilot.press(*"/tools", "enter")
            await pilot.pause(0.1)
            assert "write_file" in shown(app)
    asyncio.run(go())


def test_tui_asks_before_a_write_and_esc_says_no(tmp_path):
    import asyncio
    model = Streams(Reply(tool_calls=[call("write_file", {"path": "x.txt", "content": "one\ntwo"}, "c1")]),
                    Reply(tool_calls=[call("write_file", {"path": "y.txt", "content": "no"}, "c2")]),
                    Reply(content="done"))

    async def go():
        app = tui_on(tmp_path, model)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.press(*"write", "enter")
            await until(pilot, lambda: app.asking)
            asked = str(app.query_one("#ask").render())
            assert "write_file" in asked and "writes a file" in asked and "+ one" in asked
            await pilot.press("y")
            await until(pilot, lambda: app.asking and len(model.seen) == 2)
            assert (tmp_path / "x.txt").read_text() == "one\ntwo"
            await pilot.press("escape")                      # no to the second
            await until(pilot, lambda: not app.running)
            await pilot.pause(0.2)
            assert not (tmp_path / "y.txt").exists()
            assert "⎿ declined" in shown(app) and "done" in shown(app) and "The user declined" not in shown(app)
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


def test_tui_esc_stops_and_a_message_typed_meanwhile_waits(tmp_path):
    import asyncio
    from mafdet.agent import tui
    release = threading.Event()

    class Slow(Streams):
        started = False

        def chat(self, messages, tools=None, on_delta=None, should_stop=None, **kw):
            if not self.started:
                self.started = True
                on_delta("text", "thinking about it")
                while not should_stop() and not release.is_set():
                    release.wait(0.02)
                if should_stop():
                    from mafdet.agent.model import Stopped
                    raise Stopped
            return super().chat(messages, tools, on_delta=on_delta)

    model = Slow(Reply(content="first"), Reply(content="second"))

    async def go():
        app = tui_on(tmp_path, model)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.press(*"one", "enter")
            await until(pilot, lambda: app.running and model.started)
            await pilot.pause(0.5)
            cat = app.query_one("#activity")                 # the model works: a cat, not its thoughts
            line = str(cat.render())
            assert cat.display and line[:5] in tui.CAT_FACES and "esc to stop" in line
            assert any(word + "…" in line for word in tui.CAT_WORDS) and "writing" in line
            await pilot.press(*"two", "enter")               # the model is busy: queued
            assert app.queue == ["two"]
            release.set()
            await until(pilot, lambda: not app.running and not app.queue and len(model.seen) == 2)
            await pilot.pause(0.2)
            assert "> two" in shown(app) and "second" in shown(app) and "third" not in shown(app)
            assert not app.query_one("#activity").display

        release.clear()
        model2 = Slow(Reply(content="after the stop"))
        app = tui_on(tmp_path, model2)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.press(*"one", "enter")
            await until(pilot, lambda: app.running and model2.started)
            await pilot.press("escape")
            await until(pilot, lambda: not app.running)
            await pilot.pause(0.2)
            assert "⎿ stopped" in shown(app)
            assert [m["role"] for m in app.agent.messages] == ["system", "user"]
            await pilot.press(*"again", "enter")
            await until(pilot, lambda: not app.running and "after the stop" in shown(app))
    asyncio.run(go())


def test_the_shell_stops_with_the_turn(tmp_path):
    stop = threading.Event()
    shell = {t.name: t for t in builtin_tools(tmp_path, stop=stop)}["shell"]
    threading.Timer(0.5, stop.set).start()
    import time
    t0 = time.monotonic()
    out = shell.run({"command": "Start-Sleep 30" if sys.platform == "win32" else "sleep 30"})
    assert "stopped by the user" in out and time.monotonic() - t0 < 10
    stop.clear()
    assert "still running after 1 s" in shell.run({"command": "Start-Sleep 30" if sys.platform == "win32" else "sleep 30",
                                                   "timeout": 1})


def test_folder_instructions_reach_the_system_prompt(tmp_path):
    from mafdet import agent
    assert agent.project_notes(tmp_path) == ""
    (tmp_path / "MAFDET.md").write_text("The sample is packed with UPX.")
    assert "packed with UPX" in agent.system_prompt(tmp_path, agent.project_notes(tmp_path))
