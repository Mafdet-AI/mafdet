"""Thinking ahead in the agent (`mafdet/agent/ahead.py`) and the tool suggested while the message is
written (`kit.suggest`), on the proxy's fake engine: no GPU, no server."""
import json
import time

from mafdet.agent import mcp
from mafdet.agent.ahead import Ahead, AheadModel
from mafdet.agent.kit import Kit, suggest
from mafdet.agent.loop import Agent
from mafdet.agent.model import Reply
from mafdet.agent.permissions import Permissions
from mafdet.agent.store import Session
from mafdet.agent.tools import Tool
from proxy_fakes import FakeEngine

DRAFT = "read the file notes.txt and tell me"
SYSTEM = {"role": "system", "content": "you are an agent"}


def wait_until(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Plain:
    """The agent's ordinary model: records what reaches /v1/chat/completions."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])

    def chat(self, messages, tools=None, **kw):
        self.calls.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0) if self.replies else Reply(content="plain answer")


class FakeAgent:
    def __init__(self, messages):
        self.messages = messages
        self.toolbox = mcp.Toolbox([Tool("read_file", "Read a file.", {"type": "object", "properties": {}},
                                         lambda a: "", "read")])


def ahead_on(engine=None, messages=None):
    engine = engine or FakeEngine()
    ahead = Ahead(engine, ctx=16384)
    ahead.agent = FakeAgent(messages if messages is not None else [SYSTEM])
    return ahead, engine


def test_the_first_step_starts_from_the_notes_written_while_typing():
    ahead, engine = ahead_on()
    try:
        ahead.typed(DRAFT)
        assert wait_until(lambda: ahead.reasoned >= 8)
        assert engine.last_tools[0]["function"]["name"] == "read_file"      # the draft rendered with the tools
        assert engine.last_messages[-1] == {"role": "user", "content": DRAFT}
        assert engine.last_template_kwargs == {"enable_thinking": True}
        plain = Plain()
        model = AheadModel(plain, ahead)
        seen = []
        reply = model.chat([SYSTEM, {"role": "user", "content": DRAFT + " what it says"}], [],
                           on_delta=lambda kind, text: seen.append(kind))
        assert reply.content == "The answer is 42" and reply.reasoning.startswith("- fact1")
        assert seen[0] == "think" and "text" in seen and plain.calls == []
        sent = ahead.frame.sent
        assert engine.streamed[-len(sent):] == sent                          # notes, bridge, then SENT
        assert ahead.used == 1 and ahead.reasoned == 0                      # the notes are spent
        model.chat([SYSTEM, {"role": "user", "content": "again"}], [])
        assert len(plain.calls) == 1                                        # no notes: the ordinary way
    finally:
        ahead.close()


def test_a_tool_call_written_after_the_notes_is_a_tool_call():
    engine = FakeEngine()
    engine.final_text = ('ok </think> <tool_call> {"name": "read_file", "arguments": {"path": "notes.txt"}} '
                         '</tool_call>')
    ahead, _ = ahead_on(engine)
    try:
        ahead.typed(DRAFT)
        assert wait_until(lambda: ahead.reasoned >= 4)
        reply = AheadModel(Plain(), ahead).chat([SYSTEM, {"role": "user", "content": DRAFT}], [])
        assert reply.finish == "tool_calls" and reply.content == ""
        assert reply.tool_calls[0]["name"] == "read_file"
        assert json.loads(reply.tool_calls[0]["arguments"]) == {"path": "notes.txt"}
    finally:
        ahead.close()


def test_notes_written_after_another_conversation_are_not_used():
    ahead, engine = ahead_on()
    try:
        ahead.typed(DRAFT)
        assert wait_until(lambda: ahead.reasoned >= 4)
        plain = Plain()
        reply = AheadModel(plain, ahead).chat([SYSTEM, {"role": "assistant", "content": "earlier"},
                                               {"role": "user", "content": DRAFT}], [])
        assert reply.content == "plain answer" and len(plain.calls) == 1 and ahead.used == 0
    finally:
        ahead.close()


def test_nothing_is_reasoned_while_a_turn_runs_or_on_a_few_words():
    ahead, engine = ahead_on()
    try:
        ahead.typed("read the")
        time.sleep(0.4)
        assert ahead.reasoned == 0
        ahead.paused = True
        ahead.typed(DRAFT)
        time.sleep(0.4)
        assert ahead.reasoned == 0 and not getattr(engine, "gen_prompts", [])
        ahead.paused = False
        ahead.typed(DRAFT + " ")
        assert wait_until(lambda: ahead.reasoned >= 4)
    finally:
        ahead.close()


def test_the_agent_loop_takes_the_first_step_from_the_notes_and_the_rest_as_usual(tmp_path):
    engine = FakeEngine()
    engine.final_text = ('</think> <tool_call> {"name": "read_file", "arguments": {"path": "notes.txt"}} '
                         '</tool_call>')
    box = mcp.Toolbox([Tool("read_file", "Read a file.", {"type": "object", "properties": {}},
                            lambda a: "the notes say 42", "read")])
    plain = Plain([Reply(content="It says 42.")])
    ahead = Ahead(engine, ctx=16384)
    try:
        agent = Agent(AheadModel(plain, ahead), box, Permissions("ask"), Session(), system="you are an agent")
        ahead.agent = agent
        ahead.typed(DRAFT)
        assert wait_until(lambda: ahead.reasoned >= 4)
        assert agent.run(DRAFT) == "It says 42."
        assert [m["role"] for m in agent.messages] == ["system", "user", "assistant", "tool", "assistant"]
        assert agent.messages[3]["content"] == "the notes say 42"
        assert len(plain.calls) == 1 and plain.calls[0][-1]["role"] == "tool"   # the second step, as before
        assert "reasoning" not in json.dumps(agent.messages[2])                   # the history stays plain
    finally:
        ahead.close()


def test_the_tool_a_message_seems_to_need(tmp_path):
    k = Kit(root=tmp_path / "tools", plat="windows-x64", java=lambda: 21)
    s = suggest(k, "analizza questo eseguibile C:/samples/crack.exe e trova la password")
    assert s["id"] == "rea" and set(s["words"]) >= {".exe", "eseguibile"} and s["state"] == "install"
    assert s["mb"] == 33 + 543 + 190                                  # Node, Ghidra, rea; the JDK is here
    assert suggest(k, "apri https://example.com e guarda le richieste di rete")["id"] == "chrome-devtools-mcp"
    assert suggest(k, "aggancia frida al processo in esecuzione")["id"] == "frida-tools"
    assert suggest(k, "scrivi una funzione python che ordina una lista") is None
    assert suggest(k, "the extension .exeptional is not a file") is None
    assert suggest(k, "decompila crack.exe", attached={"rea"}) is None             # its server is on already


# ---------------------------------------------------------------- the full-screen interface

def test_tui_suggests_a_tool_ctrl_t_installs_it_and_enter_uses_the_notes(tmp_path, monkeypatch):
    import asyncio
    from test_agent import shown, until
    from test_agent_kit import make_kit
    from mafdet.agent.kit import NeedTool
    from mafdet.agent.tui import AgentTUI

    monkeypatch.setenv("MAFDET_HOME", str(tmp_path / "home"))
    k, net = make_kit(tmp_path / "home")
    k.catalog["fakere"]["tasks"] = "decompile, .exe, eseguibile"
    box = mcp.Toolbox([Tool("read_file", "Read a file.", {"type": "object", "properties": {}}, lambda a: "x", "read")])
    servers = []
    need = NeedTool(k, box, servers)
    box.active["need_tool"] = need.tool()
    box.need = need
    engine = FakeEngine()
    ahead = Ahead(engine, ctx=16384)
    plain = Plain()

    def make(emit, ask, mode="ask", fresh=False):
        return Agent(AheadModel(plain, ahead), box, Permissions(mode, ask), Session(), system="sys", emit=emit)

    async def go():
        app = AgentTUI(make, label="fake", workdir=str(tmp_path), ahead=ahead)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.press(*"decompila questo eseguibile crack.exe")
            await until(pilot, lambda: app.hint is not None)
            hint = str(app.query_one("#hint").render())
            assert "Fake RE may help" in hint and "ctrl+t installs it, ~2 MB" in hint
            await until(pilot, lambda: ahead.reasoned >= 4)                 # reasoning while typing
            await pilot.press("ctrl+t")
            await until(pilot, lambda: app.asking)
            asked = str(app.query_one("#ask").render())
            assert "MAFDET would download and install" in asked and "asked by you (ctrl+t)" in asked
            await pilot.press("y")
            await until(pilot, lambda: servers and not app.installing)
            await pilot.pause(0.2)
            assert "the MCP server fakere is on" in shown(app) and "list_procedures" in box.active
            assert app.hint is None                                          # on now: nothing to suggest
            await pilot.press("enter")
            await until(pilot, lambda: not app.running and ahead.used == 1)
            await pilot.pause(0.2)
            assert "The answer is 42" in shown(app) and plain.calls == []
            assert not ahead.paused
    try:
        asyncio.run(go())
    finally:
        ahead.close()
        for s in servers:
            s.close()
