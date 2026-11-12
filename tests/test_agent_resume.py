"""`mafdet agent --resume`: the to-do list comes back as it was last set (todo.md, next to notes.md),
done steps still done, in the model's memory and above the message box, compacted or not."""
import asyncio
import json

from mafdet.agent import mcp
from mafdet.agent.loop import Agent
from mafdet.agent.memory import Memory, memory_tools
from mafdet.agent.model import Reply
from mafdet.agent.permissions import Permissions
from mafdet.agent.store import Session
from mafdet.agent.tools import builtin_tools
from test_agent import Script, Streams, call, shown, until

STEPS = [{"task": "find main", "done": True}, {"task": "decode the key", "done": False}, {"task": "run it"}]
LISTED = "[x] find main\n[ ] decode the key\n[ ] run it"


def long_history(n=6):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find the key in check.exe"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"n": i})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"output {i} " + "x" * 1500})
    return msgs


def agent_with_memory(tmp_path, model, session, history=None, **kw):
    memory = Memory()
    box = mcp.Toolbox(builtin_tools(tmp_path) + memory_tools(memory))
    return Agent(model, box, Permissions("ask"), session, system="sys", memory=memory, history=history, **kw)


def test_the_list_is_written_whole_each_time_it_is_set(tmp_path):
    session = Session()
    memory = Memory()
    memory.clear(session.dir / "notes.md")
    memory.set_todo(STEPS)
    assert (session.dir / "todo.md").read_text(encoding="utf-8") == \
        "- [x] find main\n- [ ] decode the key\n- [ ] run it\n"
    assert Session(session.dir).todo() == [{"task": "find main", "done": True},
                                           {"task": "decode the key", "done": False},
                                           {"task": "run it", "done": False}]
    memory.set_todo([{"task": "find main", "done": True}, {"task": "decode the key", "done": True}])
    assert Session(session.dir).todo() == [{"task": "find main", "done": True}, {"task": "decode the key", "done": True}]
    memory.set_todo([])                                                  # emptied: a resume finds no list
    assert Session(session.dir).todo() == []
    assert Session().todo() == []                                        # a session that never had one
    Memory().set_todo(STEPS)                                             # no session: nothing written, no error


def test_a_resumed_session_has_its_list_back(tmp_path):
    model = Script(Reply(tool_calls=[call("todo", {"items": STEPS})]), Reply(content="started"))
    first = agent_with_memory(tmp_path, model, Session())
    assert first.run("find the key") == "started"
    assert first.memory.todo_text() == LISTED

    again = Session(first.session.dir)
    resumed = agent_with_memory(tmp_path, Script(), again, history=again.messages())
    assert resumed.memory is not first.memory and resumed.memory.todo_text() == LISTED
    assert "To-do list:\n" + LISTED in resumed.memory.text()
    # The resumed agent goes on with the same file: a step done now is there at the next resume.
    resumed.memory.set_todo([dict(s, done=True) if s["task"] == "decode the key" else s for s in STEPS])
    third = Session(again.dir)
    assert agent_with_memory(tmp_path, Script(), third, history=third.messages()).memory.todo_text() == \
        "[x] find main\n[x] decode the key\n[ ] run it"
    # A new session (/new) does not inherit it.
    assert agent_with_memory(tmp_path, Script(), Session()).memory.todo == []


def test_a_compacted_session_resumes_with_one_list_neither_lost_nor_doubled(tmp_path):
    model = Script(Reply(content="Summary: looking for the key."), Reply(content="found it"))
    session = Session()
    agent = agent_with_memory(tmp_path, model, session, ctx=4000, max_tokens=1000)
    agent.memory.set_todo(STEPS)
    agent.messages += long_history(3)[1:]
    for i in range(30):
        agent.messages.append({"role": "user", "content": f"question {i} " + "y" * 200})
        agent.messages.append({"role": "assistant", "content": f"answer {i} " + "z" * 200})
    assert agent.run("and now?") == "found it"
    assert agent.messages[1]["content"].count("To-do list:") == 1      # compacted, the list in the summary

    again = Session(session.dir)
    model2 = Script(Reply(content="Summary again."))
    resumed = agent_with_memory(tmp_path, model2, again, history=again.messages(), ctx=4000, max_tokens=1000)
    assert resumed.memory.todo_text() == LISTED                          # from todo.md, not parsed from the summary
    history = "\n".join(str(m.get("content") or "") for m in resumed.messages)
    assert history.count("To-do list:") == 1                             # the summary's, once: nothing added to it
    # Compacted again after the resume: the new summary carries the list once, as it is now.
    resumed.messages += [{"role": "user", "content": "more"}, {"role": "assistant", "content": "ok"}] * 2
    assert resumed.compact()
    summary = resumed.messages[1]["content"]
    assert "Summary again." in summary and summary.count("To-do list:") == 1 and LISTED in summary
    final = Session(session.dir)
    assert agent_with_memory(tmp_path, Script(), final, history=final.messages()).memory.todo_text() == LISTED


def test_tui_shows_the_resumed_list_above_the_message_box(tmp_path):
    from mafdet.agent.tui import AgentTUI
    session = Session()
    for m in [{"role": "system", "content": "sys"}, {"role": "user", "content": "explain a.txt"},
              {"role": "assistant", "content": "", "tool_calls": [
                  {"id": "c1", "type": "function", "function": {"name": "todo", "arguments": "{}"}}]},
              {"role": "tool", "tool_call_id": "c1", "content": "[x] read it\n[ ] explain it"},
              {"role": "assistant", "content": "Read it."}]:
        session.log(m)
    (session.dir / "todo.md").write_text("- [x] read it\n- [ ] explain it\n", encoding="utf-8")
    model = Streams(Reply(tool_calls=[call("todo", {"items": [{"task": "read it", "done": True},
                                                              {"task": "explain it", "done": True}]})]),
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
            assert "(resumed, 4 messages)" in shown(app)
            panel = app.query_one("#todo")
            text = str(panel.render())
            assert panel.display and "To do  1/2" in text and "[x] read it" in text and "[ ] explain it" in text
            assert app.agent.memory.todo == [{"task": "read it", "done": True}, {"task": "explain it", "done": False}]
            await pilot.press(*"go", "space", *"on", "enter")
            await until(pilot, lambda: not app.running and len(model.seen) == 2)
            await pilot.pause(0.3)
            assert "To do  2/2" in str(panel.render())
            assert (session.dir / "todo.md").read_text(encoding="utf-8") == "- [x] read it\n- [x] explain it\n"
    asyncio.run(go())
