"""Choosing among readings: the model, shown the code of a function and the distinct names that independent
readings gave it, picks one. The decisive test of whether a selection step is worth building (2026-11-13):
a perfect chooser among six readings would gain +0.13 on the names-only judge, a vote (the medoid) +0.02.

    python research/walk_choose.py run six                                      # the six default readings
    python research/walk_choose.py run nodraft3 --readings revise-nodraft-nothink revise-nodraft-nothink-2 revise-nodraft-bu-nothink
    python research/walk_choose.py run six-think --think
    python research/walk_choose.py run smoke --only more --limit 5                # a smoke test
    python research/walk_choose.py report research/data/walk_choose_six.json    # the tables again, no GPU
    python research/walk_choose.py prompt where 0x140003dbc                      # one request, no model

Candidates: per function with a real name, the distinct names the readings (`walk_bench.py` results in
research/data) gave it, each with the one-line summary of the first reading (in --readings order) that gave
it, and its verdicts (F1, the judge, the names-only judge) from that reading: a pick is scored with the
verdicts it already has, no judge is asked. Shuffled per function with a fixed seed (the letter of each is
recorded); how many readings gave a name is shown only with --show-votes. A function whose readings all
agree is not asked (its one name counts as the pick).

The request: the evidence the no-draft second pass gives, as of the first reading: the program's summary from
an existing no-draft book of that binary (the first --readings result that has one, else revise-nodraft-
nothink), the callers and callees under the baseline's first-pass names (the first reading's book, the
honest common ground of every candidate), the pseudo-code fetched again from rea (a private server per
binary, in a temporary MAFDET_HOME holding a copy of mcp.json: never the real ~/.mafdet) with the callees
under those names and the function itself under its decompiler's name; then the candidates as a lettered
list, and one tool, choose_name(letter). A missing or invalid call is told back once; a second failure is a
failure (never mapped to a letter) and scores as the mean single reading.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import walk_bench as wb  # noqa: E402

DATA = wb.DATA
DEFAULT_READINGS = ["baseline", "nothink", "revise-nodraft-nothink", "revise-nodraft-nothink-2",
                    "revise-nodraft-bu-nothink", "revise-nodraft-bu-cl-nothink"]
PROGRAM_SOURCE = "revise-nodraft-nothink"
HUBS = ["wmain", "GetResString", "SaveLastError", "SetReason", "GetReason", "InitGlobals", "GetInternalTemporaryBuffer"]
LETTERS = "ABCDEFGHIJKLMNOP"
CALLERS_SHOWN, CALLEES_SHOWN = 12, 60
FIXED_TOKENS, CODE_CHARS_PER_TOKEN = 700, 2.8
DEFAULT_IN_TEXT = re.compile(r"(?<![\w.])(?:sub_[0-9A-Fa-f]+|FUN_[0-9A-Fa-f]+|fcn\.[0-9A-Fa-f]+|thunk_(?:FUN|sub)_[0-9A-Fa-f]+"
                             r"|j_sub_[0-9A-Fa-f]+|nullsub_[0-9]+)(?![\w.])")

CHOOSE_SYSTEM = """You choose the name of a function of a compiled program, for a reverse engineer.

Several independent readings of the program named this function, each from the same kind of evidence. They often disagree, and a name can be fluent, specific and still wrong: a word read in one function (config, buffer, handle) travels into the names of the functions around it, and a confident reading is not a correct one.

You get what the program is, the functions that call this one, its pseudo-code with the other functions under the names a first reading gave them (they can be wrong too), the functions it calls with what they do, and the candidate names, each with the one-line summary of the reading that gave it, in no particular order.

Answer only by calling choose_name, once, with the letter of the candidate that says best what this function does. Trust the code over the names and the summaries: read what it takes, what it calls, what it returns and what it changes, and pick the name that matches that, not the one that sounds most complete. If none is right, pick the one that is least wrong."""

NO_CALL = "You did not call choose_name. Answer only with a call to choose_name(letter), no other text."


def choose_tool(letters: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": "choose_name", "description": "Choose the candidate name that says best what the function does.",
        "parameters": {"type": "object", "required": ["letter"], "properties": {
            "letter": {"type": "string", "enum": letters, "description": "the candidate's letter"}}}}}


# ---------------------------------------------------------------- candidates

def load_readings(labels: list[str], data: Path = DATA) -> dict[str, dict]:
    return {label: json.loads((data / f"walk_bench_{label}.json").read_text(encoding="utf-8")) for label in labels}


def reading_rows(results: dict[str, dict]) -> dict[tuple, list[dict]]:
    """(binary, address) -> one row per reading, in the readings' order (repeat 1 of each, scored rows only)."""
    out: dict[tuple, list[dict]] = {}
    for label, result in results.items():
        for run in result["runs"]:
            if run.get("repeat", 1) != 1:
                continue
            for r in run["functions"]:
                if r["real"] and r["status"] == "done" and r.get("given"):
                    out.setdefault((run["binary"], r["address"]), []).append({**r, "reading": label})
    n = len(results)
    return {k: v for k, v in out.items() if len(v) == n}


def candidates(rows: list[dict]) -> list[dict]:
    """The distinct names of a function's readings, in the readings' order: name, the summary and verdicts of
    the first reading that gave it, the readings that gave it (votes)."""
    out: dict[str, dict] = {}
    for r in rows:
        c = out.get(r["given"])
        if c is None:
            out[r["given"]] = {"name": r["given"], "summary": r.get("summary") or "", "readings": [r["reading"]],
                               "f1": r["f1"], "judge": r.get("judge"), "judge_name": r.get("judge_name")}
        else:
            c["readings"].append(r["reading"])
    for c in out.values():
        c["votes"] = len(c["readings"])
    return list(out.values())


def shuffled(cands: list[dict], binary: str, address: str, seed: int) -> list[dict]:
    """The candidates in an order fixed by (seed, binary, address), each with its letter and position."""
    order = list(cands)
    random.Random(f"{seed}:{binary}:{address}").shuffle(order)
    return [{**c, "letter": LETTERS[i], "position": i} for i, c in enumerate(order)]


# ---------------------------------------------------------------- the request

def function_line(g: dict, here: str = "") -> str:
    if g["address"] == here:
        return f"- {g['orig']} — this function itself (it is recursive)"
    if g["status"] == "done":
        return f"- {g['name']} — {g['summary']}"
    if g["status"] == "known":
        return f"- {g['orig']} — " + ("imported" if (g.get("flags") or {}).get("external") else
                                      "a thunk (a jump to the function of that name)"
                                      if (g.get("flags") or {}).get("thunk") else "named in the binary")
    return f"- {g['orig']} — not named yet"


class Evidence:
    """The first reading's book of a binary (first-pass names), and the program's summary."""

    def __init__(self, book: dict, program: str):
        self.book, self.program = book, program
        self.fs = book["functions"]
        self.by_int = {}
        for a in self.fs:
            n = _int(a)
            if n is not None:
                self.by_int.setdefault(n, a)
        self.callers: dict[str, list[str]] = {}
        for a, f in self.fs.items():
            for c in f.get("callees") or []:
                if c != a and c in self.fs:
                    self.callers.setdefault(c, []).append(a)
        for v in self.callers.values():
            v.sort(key=lambda x: (_int(x) is None, _int(x) or 0, x))

    def rename_in(self, code: str, but: str) -> str:
        def sub(m):
            n = _int(m.group(0).rsplit("_", 1)[-1]) if "_" in m.group(0) else None
            a = self.by_int.get(n) if n is not None else None
            g = self.fs.get(a) if a and a != but else None
            return g["name"] if g and g["status"] == "done" and g.get("name") else m.group(0)
        return DEFAULT_IN_TEXT.sub(sub, code)

    def request(self, address: str, code: str, cands: list[dict], show_votes: bool = False, ctx: int = 16384,
                max_tokens: int = 4096) -> str:
        f = self.fs[address]
        size = f" ({int(f['size'])} bytes)" if f.get("size") else ""
        head = [f"What the program is (from a first look at all of it): {self.program}"] if self.program else []
        head.append(f"Function {f['orig']} at {address}{size}.")
        callers = self.callers.get(address) or []
        if callers:
            lines = [function_line(self.fs[c]) for c in callers[:CALLERS_SHOWN]]
            if len(callers) > CALLERS_SHOWN:
                lines.append(f"- … and {len(callers) - CALLERS_SHOWN} more")
            head.append("It is called by:\n" + "\n".join(lines))
        else:
            head.append("Nothing in the program calls it directly (an entry point, or called through a pointer or a table).")
        callees = [c for c in f.get("callees") or [] if c in self.fs]
        lines = [function_line(self.fs[c], address) for c in callees[:CALLEES_SHOWN]]
        if len(callees) > CALLEES_SHOWN:
            lines.append(f"- … and {len(callees) - CALLEES_SHOWN} more")
        callee_text = "It calls:\n" + "\n".join(lines) if lines else "It calls no other function of the program."
        if f.get("outside"):
            callee_text += "\nIt also calls (imports, or not resolved): " + ", ".join(f["outside"][:30])
        cand_text = "Candidate names, from independent readings of this function:\n" + "\n".join(
            f"{c['letter']}. {c['name']} — {c['summary']}" + (f" ({c['votes']} of the readings)" if show_votes else "")
            for c in cands)
        reserve = min(max_tokens, ctx // 3)
        budget = int((ctx - reserve - FIXED_TOKENS) * CODE_CHARS_PER_TOKEN) - len("\n\n".join(head)) \
            - len(callee_text) - len(cand_text) - 900
        from mafdet.agent.context import clip
        body = clip(self.rename_in(code.replace("\r\n", "\n"), address).strip("\n"), max(1500, budget))
        return ("\n\n".join(head) + f"\n\nIts pseudo-code:\n\n```c\n{body}\n```\n\n{callee_text}\n\n{cand_text}\n\n"
                "Call choose_name with the letter of the name that says best what this function does.")


def _int(text) -> int | None:
    s = str(text or "").lower()
    s = s[2:] if s.startswith("0x") else s
    return int(s, 16) if s and len(s) <= 16 and all(ch in "0123456789abcdef" for ch in s) else None


def evidence_for(binary: str, results: dict[str, dict], data: Path = DATA) -> Evidence:
    """The first reading's book of `binary` (pass 1 of a second-pass result's kept book) and the program summary
    of the first no-draft second pass that has one."""
    order = list(results) + ([PROGRAM_SOURCE] if PROGRAM_SOURCE not in results else [])
    book = program = None
    for label in order:
        result = results.get(label)
        if result is None:
            path = data / f"walk_bench_{label}.json"
            if not path.is_file():
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("kind") != "second_pass":
            continue
        run = next((r for r in result["runs"] if r["binary"] == binary and r.get("book")), None)
        if run is None:
            continue
        if book is None:
            book = wb.pass_book(run["book"], 1)
        if not program and (run["book"].get("program") or {}).get("summary"):
            program = run["book"]["program"]["summary"]
        if book is not None and program:
            break
    if book is None:
        raise SystemExit(f"no second-pass result with a kept book for {binary}")
    return Evidence(book, program or "")


# ---------------------------------------------------------------- asking

def ask(model, system: str, user: str, letters: list[str], think: bool, max_tokens: int, temperature: float) -> dict:
    """One choice: {"letter", "status", "reason", "steps", "prompt_tokens", "completion_tokens", "seconds"}."""
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    out = {"letter": None, "status": "failed", "reason": "", "steps": 0, "prompt_tokens": 0, "completion_tokens": 0}
    tools = [choose_tool(letters)]
    for step in (1, 2):
        out["steps"] = step
        reply = model.chat(messages, tools, max_tokens=max_tokens, temperature=temperature, think=think)
        out["prompt_tokens"] += reply.prompt_tokens
        out["completion_tokens"] += reply.completion_tokens
        error = ""
        for call in reply.tool_calls:
            if call.get("name") != "choose_name":
                error = f"there is no tool {call.get('name')!r}: the only tool is choose_name"
                continue
            try:
                letter = str(json.loads(call.get("arguments") or "{}").get("letter") or "").strip().strip(".").upper()
            except (ValueError, AttributeError):
                error = "the arguments are not a JSON object"
                continue
            if letter in letters:
                out.update(letter=letter, status="done", reason="")
                out["seconds"] = round(time.perf_counter() - t0, 2)
                return out
            error = f"{letter!r} is not one of the letters {', '.join(letters)}"
        if not reply.tool_calls:
            error = "no choose_name call" + (f" (cut at {max_tokens} tokens)" if reply.finish == "length" else "")
        out["reason"] = error
        messages.append(reply.message())
        if reply.tool_calls:
            for call in reply.tool_calls:
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": f"error: {error}. Call choose_name again, with one of the letters."})
        else:
            messages.append({"role": "user", "content": NO_CALL})
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def rea_code_source(binary_path: Path, home: Path):
    """A private rea for one binary (its own process and Ghidra project), in `home` (a copy of mcp.json):
    returns (code(address) -> str, close())."""
    from mafdet.agent import mcp
    from mafdet.agent.backend import Adapter
    os.environ["MAFDET_HOME"] = str(home)
    conf = mcp.configured()["rea"]
    server = mcp.McpServer("rea", conf["command"], conf.get("args") or [], conf.get("env"), conf.get("cwd"),
                           log=str(home / "mcp-rea.log"))
    adapter = Adapter.connect(server, binary_path)
    adapter.open()

    def close():
        try:
            adapter.close()
        finally:
            server.close()
    return adapter.code, close


def private_home(work: Path | None) -> Path:
    home = Path(tempfile.mkdtemp(prefix="walk-choose-home-")) if work is None else work / "home"
    home.mkdir(parents=True, exist_ok=True)
    real = Path(os.environ.get("MAFDET_HOME") or Path.home() / ".mafdet")
    if (real / "mcp.json").is_file() and not (home / "mcp.json").is_file():
        shutil.copy(real / "mcp.json", home / "mcp.json")
    return home


def choose_all(result: dict, keys: list[tuple], rows: dict, results: dict, model, code_for, *, show_votes: bool,
               think: bool, seed: int, ctx: int, max_tokens: int, temperature: float, save=None) -> dict:
    """Ask for every function of `keys` not already in `result["functions"]`; `code_for(binary)` -> (code(address),
    close). Saves after each binary through `save(result)`."""
    done = {(f["binary"], f["address"]) for f in result["functions"]}
    for binary in dict.fromkeys(k[0] for k in keys):
        todo = [k for k in keys if k[0] == binary and k not in done]
        if not todo:
            continue
        ev = evidence_for(binary, results)
        code, close = None, None
        try:
            for k in todo:
                rs = rows[k]
                cands = shuffled(candidates(rs), binary, k[1], seed)
                entry = {"binary": binary, "address": k[1], "real": rs[0]["real"], "real_full": rs[0].get("real_full"),
                         "layer": rs[0]["layer"], "kind": rs[0]["kind"], "candidates": cands,
                         "readings": [{"reading": r["reading"], "given": r["given"]} for r in rs]}
                if len(cands) == 1:
                    entry.update(pick=cands[0]["letter"], status="unanimous")
                else:
                    if code is None:
                        code, close = code_for(binary)
                    user = ev.request(k[1], code(k[1]), cands, show_votes, ctx, max_tokens)
                    a = ask(model, CHOOSE_SYSTEM, user, [c["letter"] for c in cands], think, max_tokens, temperature)
                    entry.update(pick=a["letter"], status=a["status"], reason=a["reason"], steps=a["steps"],
                                 prompt_tokens=a["prompt_tokens"], completion_tokens=a["completion_tokens"],
                                 seconds=a.get("seconds", 0.0))
                result["functions"].append(entry)
        finally:
            if close is not None:
                close()
        if save is not None:
            save(result)
    return result


# ---------------------------------------------------------------- scoring

SCORES = {"F1": lambda c: c["f1"], "judge": lambda c: wb.JUDGE_SCORE.get(c["judge"]),
          "names": lambda c: wb.JUDGE_SCORE.get(c["judge_name"])}
GROUPS = {"all": lambda f: True, "leaf": lambda f: f["layer"] == "leaf", "middle": lambda f: f["layer"] == "middle",
          "top": lambda f: f["layer"] == "top", "program": lambda f: f["kind"] == "program",
          "runtime": lambda f: f["kind"] == "runtime"}


def medoid_name(f: dict) -> str:
    """The reading closest to the others (mean token F1 between given names; readings counted with multiplicity;
    ties: the first in --readings order)."""
    names = [r["given"] for r in f["readings"]]
    best, pick = -1.0, names[0]
    for i, a in enumerate(names):
        s = statistics.mean(wb.prf(a, b)[2] for j, b in enumerate(names) if j != i)
        if s > best + 1e-12:
            best, pick = s, a
    return pick


def values(f: dict, s: str) -> dict | None:
    """One function's numbers for score `s`: single (mean over readings, with multiplicity), medoid, oracle, pick
    (a failure scores as the mean single reading), or None when a verdict is missing."""
    val = SCORES[s]
    by = {c["name"]: c for c in f["candidates"]}
    per = [val(by[r["given"]]) for r in f["readings"]]
    if any(v is None for v in per):
        return None
    single = statistics.mean(per)
    picked = next((c for c in f["candidates"] if c["letter"] == f.get("pick")), None)
    return {"single": single, "medoid": val(by[medoid_name(f)]), "oracle": max(val(c) for c in f["candidates"]),
            "pick": val(picked) if picked is not None else single}


def boot(d: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    m = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(n))
    return round(m[int(0.025 * n)], 4), round(m[int(0.975 * n) - 1], 4)


def report(result: dict) -> dict:
    fs = result["functions"]
    out = {"n": len(fs), "asked": sum(1 for f in fs if f["status"] != "unanimous"),
           "unanimous": sum(1 for f in fs if f["status"] == "unanimous"),
           "failed": sum(1 for f in fs if f["status"] == "failed"), "groups": {}}
    for g, sel in GROUPS.items():
        out["groups"][g] = {}
        for s in SCORES:
            vs = [v for f in fs if sel(f) for v in [values(f, s)] if v is not None]
            if not vs:
                continue
            d1 = [v["pick"] - v["single"] for v in vs]
            d2 = [v["pick"] - v["medoid"] for v in vs]
            out["groups"][g][s] = {"n": len(vs), **{k: round(statistics.mean(v[k] for v in vs), 4)
                                                    for k in ("single", "medoid", "oracle", "pick")},
                                   "pick_minus_single": round(statistics.mean(d1), 4), "ci_single": boot(d1),
                                   "pick_minus_medoid": round(statistics.mean(d2), 4), "ci_medoid": boot(d2)}
    asked = [f for f in fs if f["status"] == "done"]
    for s in SCORES:
        best = [f for f in asked if values(f, s) is not None]
        out.setdefault("picked_oracle_best", {})[s] = round(statistics.mean(
            values(f, s)["pick"] == values(f, s)["oracle"] for f in best), 4) if best else None
    letters: dict[str, list[int]] = {}
    for f in asked:
        for c in f["candidates"]:
            letters.setdefault(c["letter"], [0, 0])
            letters[c["letter"]][1] += 1
            letters[c["letter"]][0] += c["letter"] == f["pick"]
    out["by_letter"] = {k: {"picked": v[0], "offered": v[1], "rate": round(v[0] / v[1], 3)} for k, v in sorted(letters.items())}
    sources: dict[str, list[int]] = {}
    for f in asked:
        picked = next(c for c in f["candidates"] if c["letter"] == f["pick"])
        for r in f["readings"]:
            sources.setdefault(r["reading"], [0, 0])
            sources[r["reading"]][1] += 1
            sources[r["reading"]][0] += r["given"] == picked["name"]
    out["by_reading"] = {k: {"picked": v[0], "of": v[1], "rate": round(v[0] / v[1], 3)} for k, v in sources.items()}
    out["hubs"] = []
    for f in fs:
        if f["binary"] == "where" and f["real"] in HUBS:
            out["hubs"].append({"real": f["real"], "address": f["address"], "pick": f.get("pick"), "status": f["status"],
                                "candidates": [{k: c[k] for k in ("letter", "name", "votes", "f1", "judge", "judge_name")}
                                               for c in f["candidates"]]})
    return out


def report_text(result: dict) -> str:
    rep = report(result)
    lines = [f"walk choose {result['label']}: readings {', '.join(result['readings'])}; {rep['n']} functions, "
             f"{rep['asked']} asked, {rep['unanimous']} unanimous, {rep['failed']} failed"
             + (" (votes shown)" if result.get("show_votes") else "") + (", thinking" if result.get("think") else "")]
    for g, scores in rep["groups"].items():
        lines.append(f"\n[{g}]" + "".join(f"{s:>46}" for s in scores))
        n = next(iter(scores.values()))["n"] if scores else 0
        lines.append(f"  n {n}")
        for k in ("single", "medoid", "oracle", "pick"):
            lines.append(f"  {k:<8}" + "".join(f"{scores[s][k]:>46.3f}" for s in scores))
        lines.append("  pick-single" + "".join(
            f"{scores[s]['pick_minus_single']:>+29.3f} ({scores[s]['ci_single'][0]:+.3f}..{scores[s]['ci_single'][1]:+.3f})"
            for s in scores))
        lines.append("  pick-medoid" + "".join(
            f"{scores[s]['pick_minus_medoid']:>+29.3f} ({scores[s]['ci_medoid'][0]:+.3f}..{scores[s]['ci_medoid'][1]:+.3f})"
            for s in scores))
    lines.append("\npicked the oracle-best: " + ", ".join(f"{s} {v}" for s, v in rep["picked_oracle_best"].items()))
    lines.append("by letter (picked/offered): " + ", ".join(f"{k} {v['picked']}/{v['offered']} ({v['rate']:.2f})"
                                                             for k, v in rep["by_letter"].items()))
    lines.append("by source reading (its name picked, of the functions asked): " + ", ".join(
        f"{k} {v['rate']:.2f}" for k, v in rep["by_reading"].items()))
    for h in rep["hubs"]:
        lines.append(f"\n{h['real']} ({h['address']}): pick {h['pick']} ({h['status']})")
        for c in h["candidates"]:
            mark = " <- picked" if c["letter"] == h["pick"] else ""
            lines.append(f"  {c['letter']}. {c['name']:<48} votes {c['votes']}  F1 {c['f1']:.2f}  {c['judge']:<9} "
                         f"{c['judge_name']}{mark}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the commands

def select(rows: dict, only: str, limit: int) -> list[tuple]:
    keys = sorted(rows, key=lambda k: (k[0], _int(k[1]) or 0))
    if only:
        wanted = set(only.split(","))
        keys = [k for k in keys if k[0] in wanted]
    if limit:
        keys = keys[:limit]
    return keys


def cmd_run(a) -> int:
    results = load_readings(a.readings)
    rows = reading_rows(results)
    keys = select(rows, a.only, a.limit)
    out = Path(a.out) if a.out else DATA / f"walk_choose_{a.label}.json"
    base = json.loads((DATA / "walk_bench_baseline.json").read_text(encoding="utf-8"))
    model_name, ctx = a.model or base.get("alias") or base.get("model"), a.ctx or base.get("ctx") or 16384
    result = {"label": a.label, "readings": a.readings, "show_votes": a.show_votes, "think": a.think, "seed": a.seed,
              "model": model_name, "ctx": ctx, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "functions": []}
    if out.is_file() and not a.restart:
        old = json.loads(out.read_text(encoding="utf-8"))
        if old.get("readings") == a.readings and old.get("show_votes") == a.show_votes and old.get("think") == a.think:
            result = old                                    # goes on where it stopped
            print(f"going on: {len(result['functions'])} functions done before")
    paths = {s["name"]: s["path"] for s in wb.load_set(Path(a.set))}
    home = private_home(Path(a.work) if a.work else None)

    def save(r):
        r["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)
        st = report(r)
        al = st["groups"].get("all", {}).get("names")
        print(f"[{time.strftime('%H:%M:%S')}] {len(r['functions'])} functions"
              + (f"; names-only single {al['single']:.3f} medoid {al['medoid']:.3f} pick {al['pick']:.3f} oracle "
                 f"{al['oracle']:.3f}" if al else ""), flush=True)
    engine = None
    try:
        from mafdet.agent.model import ChatModel
        if a.url:
            url, alias = a.url, model_name
        else:
            engine, alias = wb.start_engine(model_name, ctx, a.port)
            url = engine.url
        model = ChatModel(url, alias)
        choose_all(result, keys, rows, results, model, lambda b: rea_code_source(Path(paths[b]), home),
                   show_votes=a.show_votes, think=a.think, seed=a.seed, ctx=ctx, max_tokens=a.max_tokens,
                   temperature=a.temperature, save=save)
    finally:
        if engine is not None:
            engine.close()
    print(report_text(result))
    print(f"\n{out}")
    return 0


def cmd_report(a) -> int:
    print(report_text(json.loads(Path(a.result).read_text(encoding="utf-8"))))
    return 0


def cmd_prompt(a) -> int:
    """One function's request, built with no model (rea started privately for the pseudo-code)."""
    results = load_readings(a.readings)
    rows = reading_rows(results)
    k = (a.binary, a.address)
    cands = shuffled(candidates(rows[k]), a.binary, a.address, a.seed)
    ev = evidence_for(a.binary, results)
    paths = {s["name"]: s["path"] for s in wb.load_set(Path(a.set))}
    code, close = rea_code_source(Path(paths[a.binary]), private_home(None))
    try:
        user = ev.request(a.address, code(a.address), cands, a.show_votes)
    finally:
        close()
    text = f"SYSTEM:\n{CHOOSE_SYSTEM}\n\nTOOL:\n{json.dumps(choose_tool([c['letter'] for c in cands]))}\n\nUSER:\n{user}"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask the model to choose, for every function with a real name")
    r.add_argument("label")
    for p in (r,):
        p.add_argument("--readings", nargs="+", default=DEFAULT_READINGS)
        p.add_argument("--show-votes", action="store_true", help="show how many readings gave each name")
        p.add_argument("--seed", type=int, default=0)
    r.add_argument("--think", action="store_true", help="let the model think (default: --no-think, as the walks)")
    r.add_argument("--only", default="", help="comma-separated binary names of the set")
    r.add_argument("--limit", type=int, default=0, help="the first N functions only (a smoke test)")
    r.add_argument("--model", default="")
    r.add_argument("--ctx", type=int, default=0)
    r.add_argument("--port", type=int, default=8079)
    r.add_argument("--url", default="", help="an engine already running")
    r.add_argument("--max-tokens", type=int, default=4096)
    r.add_argument("--temperature", type=float, default=0.6)
    r.add_argument("--set", default=str(wb.DEFAULT_SET))
    r.add_argument("--work", default="", help="where the private MAFDET_HOME goes (default: a temporary folder)")
    r.add_argument("--out", default="")
    r.add_argument("--restart", action="store_true", help="start again instead of going on")
    p = sub.add_parser("report", help="the tables of a result, no GPU")
    p.add_argument("result")
    q = sub.add_parser("prompt", help="one function's request, no model")
    q.add_argument("binary")
    q.add_argument("address")
    q.add_argument("--readings", nargs="+", default=DEFAULT_READINGS)
    q.add_argument("--show-votes", action="store_true")
    q.add_argument("--seed", type=int, default=0)
    q.add_argument("--set", default=str(wb.DEFAULT_SET))
    q.add_argument("--out", default="")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "report": cmd_report, "prompt": cmd_prompt}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
