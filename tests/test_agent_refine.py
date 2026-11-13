"""`mafdet agent --walk --refine`: the second pass from the top down, with the callers and their call lines,
and the paragraph on the whole program, on the fake decompiler of `agent_fake_re.py` and a scripted model."""
import json
import re

from mafdet.agent.model import Reply
from mafdet.agent.walk import PARAGRAPH_SYSTEM, REFINE_SYSTEM, WALK_SYSTEM

from test_agent_walk import ANALYSED, calls_logged, good, walk_on

CALLING = ["0x1000", "0x2000", "0x2100", "0x3000", "0x3100", "0x3200", "0x6000", "0x6100", "0x9000"]
LEAVES = ["0x2200", "0x3300", "0x4000", "0x8000"]       # 0x3300 calls only the import, 0x8000 only itself


class Reader:
    """First pass: fn_<address>. Second: the names in `renames`, the same name elsewhere, nothing at all for
    the addresses in `silent`. The program: one paragraph."""

    def __init__(self, renames=None, silent=()):
        self.renames = {int(a, 16): n for a, n in (renames or {}).items()}     # rea's 0x3000, pyghidra's 00003000
        self.silent = {int(a, 16) for a in silent}
        self.first, self.second, self.program = [], [], []

    def chat(self, messages, tools=None, should_stop=None, **kw):
        system, user = messages[0]["content"], messages[1]["content"]
        if system == PARAGRAPH_SYSTEM:
            self.program.append(user)
            return Reply(content="<think>x</think>A small test program that prints a number.",
                         prompt_tokens=300, completion_tokens=40)
        addr = re.match(r"Function \S+ at (\S+?)(?: \(|:)", user).group(1)
        if system == WALK_SYSTEM:
            self.first.append((addr, user))
            reply = good(addr)
        else:
            assert system == REFINE_SYSTEM
            self.second.append((addr, user))
            if int(addr, 16) in self.silent:
                reply = Reply(content="I am not sure.")
            else:
                name = self.renames.get(int(addr, 16)) or f"fn_{int(addr, 16):x}"
                reply = Reply(tool_calls=[{"id": "c1", "name": "record_function", "arguments": json.dumps(
                    {"name": name, "summary": f"read again {addr}", "confidence": "medium"})}])
        reply.prompt_tokens, reply.completion_tokens = 100, 20
        return reply

    def prompt(self, addr):
        return next(u for a, u in self.second if a == addr)


def test_the_second_pass_reads_the_callers_from_the_top_down(tmp_path):
    model = Reader(renames={"0x1000": "main", "0x3000": "checksum_options"}, silent={"0x2000"})
    w, result, out = walk_on(tmp_path, model, refine=True)
    fs = w.book.functions
    assert len(model.first) == ANALYSED
    order = list(dict.fromkeys(a for a, _ in model.second))
    assert [a for a, _ in model.second].count("0x2000") == 2               # told back once, as in the first pass
    assert sorted(order) == sorted(CALLING) and not set(order) & set(LEAVES)
    pos = {a: i for i, a in enumerate(order)}
    assert order[0] in ("0x1000", "0x4000") and pos["0x1000"] < pos["0x2000"] < pos["0x2100"]   # top down
    assert pos["0x3000"] < min(pos["0x3100"], pos["0x3200"])

    top = model.prompt("0x1000")
    assert "Nothing in the program calls it directly" in top
    assert "The first reading named it fn_1000 [high confidence]: does 0x1000" in top
    assert "Function fn_1000 at 0x1000 (was FUN_00001000" in top and "int fn_1000(int param_1)" in top
    mid = model.prompt("0x3100")
    assert "It is called by:\n- checksum_options (was FUN_00003000, first read as fn_3000) — read again 0x3000" in mid
    assert "    `param_1 = fn_3100(param_1);`" in mid                 # the line of the caller that calls it
    assert "int checksum_options" not in mid                           # not the caller's own signature
    assert "fn_3300 — does 0x3300" in mid                              # its callees, as in the first pass
    leaf_caller = model.prompt("0x2100")
    assert "- fn_2000 (was FUN_00002000) — does 0x2000" in leaf_caller   # 0x2000's second reading failed

    assert fs["0x1000"]["name"] == "main" and fs["0x1000"]["refine"] == "renamed"
    assert fs["0x1000"]["first"] == {"name": "fn_1000", "summary": "does 0x1000", "comment": "",
                                     "confidence": "high"}
    assert fs["0x2100"]["refine"] == "kept" and fs["0x2100"]["summary"] == "read again 0x2100"
    assert fs["0x2000"]["refine"].startswith("failed: no record_function call") and fs["0x2000"]["name"] == "fn_2000"
    assert "refine" not in fs["0x2200"] and fs["0x2200"]["summary"] == "does 0x2200"   # a leaf, untouched
    assert fs["0x1000"]["prompt_tokens"] == 200                         # both readings counted

    assert len(model.program) == 1
    prog = model.program[0]
    assert prog.startswith("Program target.exe (") and "- main (was FUN_00001000, first read as fn_1000)" in prog
    assert "  - checksum_options" in prog and "It imports: printf" in prog
    assert w.book.data["program"]["summary"] == "A small test program that prints a number."
    md = (w.book.dir / "book.md").read_text(encoding="utf-8")
    assert md.index("## The program") < md.index("## From the roots down")
    assert "A small test program that prints a number." in md and "**main** `0x1000`" in md
    assert "first read as fn_3000" in md
    assert "What the program is (a model's reading): A small test program" in w.book.lookup()
    assert re.search(r"\[1/9\] 0x\w+ fn_\w+ (kept|-> \w+)", out)
    assert "second pass: 6 kept, 2 renamed, 1 failed" in out and not result["stopped"]
    assert "the program: A small test program" in out

    again = Reader()                                                    # rerun: nothing left but the failed one
    w2, _, out2 = walk_on(tmp_path, again, refine=True)
    assert again.first == [] and again.second == [] and again.program == []
    retry = Reader()
    w3, _, _ = walk_on(tmp_path, retry, refine=True, retry_failed=True)
    assert [a for a, _ in retry.second] == ["0x2000"] and w3.book.functions["0x2000"]["refine"] == "kept"
    assert len(retry.program) == 1                                     # something changed: written again


def test_no_second_pass_while_functions_are_left(tmp_path):
    model = Reader()
    w, result, out = walk_on(tmp_path, model, refine=True, max_functions=5)
    assert len(model.first) == 5 and model.second == [] and model.program == []
    assert "second pass: not yet, 8 functions are still to be named" in out


def test_max_functions_counts_both_passes_and_the_next_run_goes_on(tmp_path):
    capped = Reader()
    w, result, out = walk_on(tmp_path, capped, refine=True, max_functions=ANALYSED + 3)
    assert len(capped.first) == ANALYSED and len(capped.second) == 3 and capped.program == []
    assert result["stopped"] == "max functions" and "--max-functions reached: 6 functions left" in out
    rest = Reader()
    w, result, out = walk_on(tmp_path, rest, refine=True)
    assert rest.first == [] and len(rest.second) == 6 and len(rest.program) == 1 and not result["stopped"]


def test_a_rename_of_the_second_pass_reaches_the_decompiler_and_the_code_after_it(tmp_path):
    """pyghidra-mcp: the first pass wrote fn_3000 into the project, so the code of 0x3000's callers says
    fn_3000; once the second pass calls it checksum_options, what the model reads says that."""
    log = tmp_path / "calls.jsonl"
    model = Reader(renames={"0x3000": "checksum_options"})
    from mafdet.agent.permissions import Permissions
    w, result, out = walk_on(tmp_path, model, mode="pyghidra", refine=True, permissions=Permissions("all"),
                             env={"FAKE_RE_LOG": str(log), "FAKE_RE_PROJECT": str(tmp_path / "project.json")})
    renames = [c["args"] for c in calls_logged(log) if c["name"] == "rename_function"]
    news = [r.get("new_name") for r in renames]
    assert news.count("fn_3000") == 1 and news.count("checksum_options") == 1
    assert news.count("fn_2100") == 1 and len(news) == ANALYSED + 1     # kept, summary changed: no second rename
    comments = [c for c in calls_logged(log) if c["name"] == "set_comment"]
    assert len(comments) == ANALYSED + 9                                 # every summary read again changed
    fs = w.book.functions
    a3000 = next(a for a, f in fs.items() if f["name"] == "checksum_options")
    assert fs[a3000]["mirror"]["rename"] == "written"
    a1000 = next(a for a, f in fs.items() if f["orig"].endswith("1000"))
    assert w.rename_in("x = fn_3000(1);") == "x = checksum_options(1);"
    assert "- checksum_options (was FUN_00003000, first read as fn_3000)" in model.program[0]
    assert fs[a1000]["refine"] == "kept"
