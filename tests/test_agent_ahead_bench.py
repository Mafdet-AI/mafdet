"""research/agent_ahead.py, the measure of thinking ahead in the agent (plan item B1), on the proxy's fake
engine and a scripted model: no GPU, no server."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

import agent_ahead as aa  # noqa: E402
from mafdet.agent.ahead import Ahead  # noqa: E402
from mafdet.agent.model import Reply  # noqa: E402
from proxy_fakes import FakeEngine  # noqa: E402

TOOL_CALL = '</think> <tool_call> {"name": "read_file", "arguments": {"path": "notes.txt"}} </tool_call>'


class Scripted:
    """The agent's plain model: a read_file call when the user has just spoken, an answer after a result."""

    def __init__(self, answer="Giulia Rossi owns the installer."):
        self.answer = answer
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if messages[-1]["role"] == "user":
            return Reply(tool_calls=[{"id": f"c{self.calls}", "name": "read_file",
                                      "arguments": json.dumps({"path": "notes.txt"})}],
                         finish="tool_calls", completion_tokens=20)
        return Reply(content=self.answer, finish="stop", completion_tokens=10)


def bench_on(tmp_path, model=None):
    engine = FakeEngine()
    engine.final_text = TOOL_CALL
    ahead = Ahead(engine, ctx=16384)
    workdir = aa.make_workdir(tmp_path / "project")
    bench = aa.Bench(model or Scripted(), ahead, workdir, cps=400.0, settle=0.4, scratch=tmp_path / "scratch")
    return bench, ahead


def test_right_answers():
    assert aa.right("About 3,550 euros are left.", ["3550"])
    assert aa.right("3.550 euro", ["3550"]) and aa.right("3550", ["3550"])
    assert not aa.right("35500 euros", ["3550"]) and not aa.right("13 tasks", ["3"])
    assert aa.right("It is Giulia Rossi.", ["giulia"]) and not aa.right("Marco Bianchi", ["giulia"])
    assert aa.right("Version 2.1 came out on 2026-03-14.", ["2026-03-14"])


def test_the_work_folder_holds_the_answers(tmp_path):
    w = aa.make_workdir(tmp_path / "p")
    rows = list(csv.DictReader((w / "sales.csv").open(encoding="utf-8")))
    units = {r["month"]: int(r["units"]) for r in rows}
    inv = list(csv.DictReader((w / "inventory.csv").open(encoding="utf-8")))
    answers = {r[0]: r[1] for r in aa.REQUESTS}
    assert answers["read sales.csv and tell me the total number of units sold"] == [str(sum(units.values()))]
    assert answers["in sales.csv, which month sold the most units?"] == [max(units, key=units.get)]
    total = sum(int(r["quantity"]) * int(r["price"]) for r in inv)
    assert answers["read inventory.csv and tell me the total value of the items, quantity times price"] == [str(total)]
    assert (w / "todo.md").read_text(encoding="utf-8").count("- [ ]") == 3
    assert len((w / "poem.txt").read_text(encoding="utf-8").splitlines()) == 7
    assert "ERROR 4711" in (w / "logs" / "server-b.log").read_text(encoding="utf-8")
    assert 10 <= len(aa.REQUESTS) <= 15


def test_the_typist_sends_every_prefix_on_the_clock():
    seen, slept, now = [], [], [0.0]

    def sleep(s):
        slept.append(s)
        now[0] += s
    aa.Typist(cps=10.0, sleep=sleep, clock=lambda: now[0], pauses=False).type("read it", seen.append)
    assert seen == ["r", "re", "rea", "read", "read ", "read i", "read it"]
    assert 0.5 < sum(slept) <= 0.7 + 1e-6 and abs(aa.Typist(cps=10.0, pauses=False).seconds("read it") - 0.7) < 1e-9


def test_the_typist_pauses_as_a_person_and_the_same_for_both_configs():
    """A pause after punctuation, longer than ahead.DEBOUNCE, so the draft is rendered while typing; the
    schedule depends only on the text, so the plain run waits exactly as long."""
    from mafdet.agent.ahead import DEBOUNCE
    text = "read notes.txt, then tell me the owner. And the budget?"
    t = aa.Typist(cps=12.0)
    times = t.schedule(text)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) > DEBOUNCE + 0.4 and t.seconds(text) == times[-1] == aa.Typist(cps=12.0).seconds(text)
    assert t.seconds(text) > len(text) / 12.0


def test_both_configs_measured_and_the_notes_used_only_ahead(tmp_path):
    model = Scripted()
    bench, ahead = bench_on(tmp_path, model)
    try:
        req = aa.REQUESTS[0]
        a = bench.run(req, "ahead")
        assert a["notes_used"] and a["reasoned_ahead"] > 0
        assert a["first_tool"] == "read_file" and a["first_tool_expected"] and a["tools"] == ["read_file"]
        assert a["right"] and a["answer"] == "Giulia Rossi owns the installer."
        assert 0 <= a["first_tool_s"] <= a["answer_s"] and a["steps"] == 2 and not a["errors"]
        assert model.calls == 1                       # the first step came from the notes, the second as usual
        p = bench.run(req, "plain")
        assert not p["notes_used"] and p["reasoned_ahead"] == 0 and p["right"] and model.calls == 3
        assert p["first_tool"] == "read_file" and p["answer_s"] >= p["first_tool_s"] >= 0
        assert not ahead.paused                       # left ready for the next request
    finally:
        ahead.close()


def test_a_wrong_answer_and_no_tool_are_rows_too(tmp_path):
    class Answers:
        def chat(self, messages, tools=None, **kw):
            return Reply(content="I think it is Marco.", finish="stop")
    bench, ahead = bench_on(tmp_path, Answers())
    try:
        p = bench.run(aa.REQUESTS[0], "plain")
        assert not p["right"] and p["first_tool"] is None and p["first_tool_s"] is None and not p["first_tool_expected"]
    finally:
        ahead.close()


def test_parts_append_resume_and_summarize(tmp_path):
    model = Scripted()
    bench, ahead = bench_on(tmp_path, model)
    out = tmp_path / "out.json"
    said = []
    try:
        aa.run_part(bench, out, first=0, n=1, setup={"model": "fake"}, say=said.append)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert [r["config"] for r in data["rows"]] == ["ahead", "plain"]         # request 0: ahead first
        assert data["setup"] == {"model": "fake"} and data["summary"]["paired"]["requests"] == 1
        calls = model.calls
        aa.run_part(bench, out, first=0, n=1, say=said.append)                    # done already: nothing runs
        assert model.calls == calls and len(json.loads(out.read_text(encoding="utf-8"))["rows"]) == 2
        aa.run_part(bench, out, first=1, n=1, say=said.append)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert [(r["index"], r["config"]) for r in data["rows"][2:]] == [(1, "plain"), (1, "ahead")]  # alternates
        data["rows"] = [r for r in data["rows"] if not (r["index"] == 1 and r["config"] == "ahead")]
        out.write_text(json.dumps(data), encoding="utf-8")
        aa.run_part(bench, out, first=0, n=2, say=said.append)                    # only the missing one runs
        data = json.loads(out.read_text(encoding="utf-8"))
        assert sorted((r["index"], r["config"]) for r in data["rows"]) == [
            (0, "ahead"), (0, "plain"), (1, "ahead"), (1, "plain")]
        s = data["summary"]
        assert s["ahead"]["runs"] == 2 and s["plain"]["runs"] == 2 and s["ahead"]["notes_used"] == 2
        assert s["paired"]["requests"] == 2 and s["paired"]["first_tool_changed"] == 0
        assert s["ahead"]["right"] == 1 and s["plain"]["right"] == 1        # request 1 asks for 3550, not Giulia
        assert "paired over 2 requests" in said[-1] and "ahead" in said[-1]
    finally:
        ahead.close()
