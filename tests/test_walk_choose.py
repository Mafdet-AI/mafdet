"""research/walk_choose.py: candidates, the shuffle, the request, the choice, the scoring, end to end with a fake
model and a fake decompiler. No GPU, no decompiler, never the real ~/.mafdet."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import walk_choose as wc  # noqa: E402
from mafdet.agent.model import Reply  # noqa: E402


def row(addr, given, f1, judge, jn, layer="leaf", real="X", summary=None):
    return {"address": addr, "real": real, "real_full": real, "given": given, "summary": summary or f"{given} does it",
            "status": "done", "layer": layer, "kind": "program", "f1": f1, "judge": judge, "judge_name": jn}


def fixtures(tmp_path):
    """Two readings of three functions (0x10 calls 0x20; 0x30 alone): `ra` a first-pass result, `rb` a second
    pass with its kept book (first-pass names in `first`, a program summary)."""
    ra = {"label": "ra", "runs": [{"binary": "x", "repeat": 1, "functions": [
        row("0x10", "parse_options", 0.5, "related", "related", "top", "ProcessOptions"),
        row("0x20", "is_console_handle", 1.0, "same", "same", "leaf", "IsConsoleHandle"),
        row("0x30", "match_pattern", 0.5, "related", "different", "leaf", "Match")]}]}
    book = {"binary": {"name": "x.exe"}, "program": {"summary": "A small tool that parses options."}, "functions": {
        "0x10": {"address": "0x10", "orig": "FUN_00000010", "name": "process_options", "summary": "processes them",
                 "status": "done", "callees": ["0x20"], "outside": ["printf"], "size": 40, "flags": {},
                 "first": {"name": "parse_options", "summary": "parses the options", "comment": "", "confidence": "medium"}},
        "0x20": {"address": "0x20", "orig": "FUN_00000020", "name": "is_console_handle", "summary": "is it a console",
                 "status": "done", "callees": [], "outside": [], "size": 10, "flags": {}},
        "0x30": {"address": "0x30", "orig": "FUN_00000030", "name": "match_pattern", "summary": "matches",
                 "status": "done", "callees": [], "outside": [], "size": 10, "flags": {}}}}
    rb = {"label": "rb", "kind": "second_pass", "runs": [{"binary": "x", "repeat": 1, "book": book, "functions": [
        row("0x10", "process_options", 1.0, "same", "same", "top", "ProcessOptions"),
        row("0x20", "is_console_handle", 1.0, "same", "same", "leaf", "IsConsoleHandle"),
        row("0x30", "match_pattern", 0.5, "related", "different", "leaf", "Match")]}]}
    for name, data in (("ra", ra), ("rb", rb)):
        (tmp_path / f"walk_bench_{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return wc.load_readings(["ra", "rb"], tmp_path)


def test_candidates_dedupe_and_remember_votes(tmp_path):
    results = fixtures(tmp_path)
    rows = wc.reading_rows(results)
    assert set(rows) == {("x", "0x10"), ("x", "0x20"), ("x", "0x30")}
    c = wc.candidates(rows[("x", "0x10")])
    assert [x["name"] for x in c] == ["parse_options", "process_options"] and [x["votes"] for x in c] == [1, 1]
    c = wc.candidates(rows[("x", "0x20")])
    assert len(c) == 1 and c[0]["votes"] == 2 and c[0]["readings"] == ["ra", "rb"]
    assert c[0]["summary"] == "is_console_handle does it"                  # the first reading's summary


def test_the_shuffle_is_fixed_per_function_and_seed():
    cands = [{"name": n} for n in "abcdef"]
    one = wc.shuffled(cands, "x", "0x10", 0)
    assert one == wc.shuffled(cands, "x", "0x10", 0)                       # reproducible
    assert [c["letter"] for c in one] == list("ABCDEF") and [c["position"] for c in one] == list(range(6))
    orders = {tuple(c["name"] for c in wc.shuffled(cands, "x", a, 0)) for a in ("0x10", "0x20", "0x30", "0x40")}
    assert len(orders) > 1                                                 # it differs between functions
    assert sorted(c["name"] for c in one) == list("abcdef")


def test_the_request(tmp_path):
    results = fixtures(tmp_path)
    ev = wc.evidence_for("x", results, tmp_path)
    rows = wc.reading_rows(results)
    cands = wc.shuffled(wc.candidates(rows[("x", "0x10")]), "x", "0x10", 0)
    code = "int FUN_00000010(int a)\r\n{\r\n  return FUN_00000020(a) + FUN_00000030(a);\r\n}"
    req = ev.request("0x10", code, cands)
    assert req.startswith("What the program is (from a first look at all of it): A small tool that parses options."
                          "\n\nFunction FUN_00000010 at 0x10 (40 bytes).\n\nNothing in the program calls it")
    # The function under its decompiler's name; the others under the first reading's names.
    assert "int FUN_00000010(int a)" in req and "is_console_handle(a) + match_pattern(a)" in req
    assert "It calls:\n- is_console_handle — is it a console\nIt also calls (imports, or not resolved): printf" in req
    letters = {c["name"]: c["letter"] for c in cands}
    assert f"{letters['parse_options']}. parse_options — parse_options does it" in req
    assert "of the readings" not in req and req.endswith("Call choose_name with the letter of the name that says "
                                                         "best what this function does.")
    assert "(1 of the readings)" in ev.request("0x10", code, cands, show_votes=True)
    req20 = ev.request("0x20", "int FUN_00000020(void)\n{\n  return 1;\n}", wc.shuffled(
        wc.candidates(rows[("x", "0x20")]), "x", "0x20", 0))
    assert "It is called by:\n- parse_options — parses the options" in req20      # the caller's first reading


class FakeModel:
    """Picks the candidate named `want`, after `bad` wrong answers."""

    def __init__(self, want="process_options", bad=()):
        self.want, self.bad, self.seen = want, list(bad), []

    def chat(self, messages, tools=None, **kw):
        self.seen.append((json.loads(json.dumps(messages)), tools, kw))
        if self.bad:
            reply = self.bad.pop(0)
        else:
            letter = re.search(rf"^([A-P])\. {self.want} —", messages[1]["content"], re.M).group(1)
            reply = Reply(tool_calls=[{"id": "c1", "name": "choose_name", "arguments": json.dumps({"letter": letter})}])
        reply.prompt_tokens, reply.completion_tokens = 100, 5
        return reply


def test_ask_retries_once_and_never_maps_an_invalid_letter():
    tools_letters = ["A", "B"]
    m = FakeModel(bad=[Reply(content="I think B.")])
    m.want = "x"
    user = "A. x — it\nB. y — it"
    a = wc.ask(m, wc.CHOOSE_SYSTEM, user, tools_letters, False, 1024, 0.6)
    assert a["letter"] == "A" and a["status"] == "done" and a["steps"] == 2
    assert m.seen[1][0][-1]["content"].startswith("You did not call choose_name")
    assert m.seen[0][1][0]["function"]["parameters"]["properties"]["letter"]["enum"] == ["A", "B"]
    assert m.seen[0][2]["think"] is False
    bad = lambda: Reply(tool_calls=[{"id": "c1", "name": "choose_name", "arguments": '{"letter": "Z"}'}])
    m = FakeModel(bad=[bad(), bad()])
    a = wc.ask(m, wc.CHOOSE_SYSTEM, user, tools_letters, False, 1024, 0.6)
    assert a["letter"] is None and a["status"] == "failed" and "'Z' is not one of the letters A, B" in a["reason"]


def test_end_to_end_and_the_scores(tmp_path):
    results = fixtures(tmp_path)
    rows = wc.reading_rows(results)
    keys = wc.select(rows, "", 0)
    fetched, closed, saved = [], [], []

    def code_for(binary):
        def code(address):
            fetched.append(address)
            return f"int FUN_{int(address, 16):08x}(int a)\n{{\n  return a;\n}}"
        return code, lambda: closed.append(binary)
    result = {"label": "t", "readings": ["ra", "rb"], "functions": []}
    model = FakeModel()
    wc.choose_all(result, keys, rows, results, model, code_for, show_votes=False, think=False, seed=0, ctx=8192,
                  max_tokens=1024, temperature=0.6, save=lambda r: saved.append(len(r["functions"])))
    assert fetched == ["0x10"] and closed == ["x"] and saved == [3]        # only the function readings disagree on
    by = {f["address"]: f for f in result["functions"]}
    assert by["0x20"]["status"] == "unanimous" and by["0x10"]["status"] == "done"
    assert next(c for c in by["0x10"]["candidates"] if c["letter"] == by["0x10"]["pick"])["name"] == "process_options"
    rep = wc.report(result)
    names = rep["groups"]["all"]["names"]
    # 0x10: single (0.5 + 1)/2, medoid the first reading (ties), oracle 1, pick 1; 0x20 1 everywhere; 0x30 0.
    assert (names["single"], names["medoid"], names["oracle"], names["pick"]) == (0.5833, 0.5, 0.6667, 0.6667)
    assert rep["groups"]["top"]["F1"]["pick_minus_single"] == 0.25 and rep["picked_oracle_best"]["names"] == 1.0
    assert rep["by_reading"] == {"ra": {"picked": 0, "of": 1, "rate": 0.0}, "rb": {"picked": 1, "of": 1, "rate": 1.0}}
    # Going on: nothing asked again.
    wc.choose_all(result, keys, rows, results, model, code_for, show_votes=False, think=False, seed=0, ctx=8192,
                  max_tokens=1024, temperature=0.6)
    assert fetched == ["0x10"] and len(result["functions"]) == 3
    # A failure scores as the mean single reading, and is counted.
    result["functions"][0].update(pick=None, status="failed")
    rep = wc.report(result)
    assert rep["failed"] == 1 and rep["groups"]["top"]["names"]["pick"] == rep["groups"]["top"]["names"]["single"]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(result), encoding="utf-8")
    assert wc.main(["report", str(p)]) == 0


def test_private_home_copies_mcp_json_and_never_writes_the_real_one(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (real / "mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MAFDET_HOME", str(real))
    home = wc.private_home(tmp_path / "work")
    assert home == tmp_path / "work" / "home" and (home / "mcp.json").read_text() == "{}"
    assert sorted(p.name for p in real.iterdir()) == ["mcp.json"]
