"""Thinking ahead in the agent (plan item B1): does reasoning while the request is typed shorten the wait
after Enter, and does it change the first tool or the answer?

Thirteen agent requests that need a tool first (read a file and answer, search a folder, sum a CSV...), on a
small work folder this script writes itself, so they run on any machine. Each request is typed by a typist
at --cps characters a second, twice:

  ahead  -- every keystroke goes to `ahead.Ahead.typed`, as the full-screen interface sends it
            (`tui.py`: on_change -> ahead.typed), so the model writes notes while the request is typed;
            on Enter the turn's first step starts from them (`AheadModel`), the rest as usual;
  plain  -- the same typing time waited with nothing sent, then Enter: the agent as it is with
            `--no-think-ahead`.

Each run is a fresh agent (new session, notes, shell) on the same work folder. The two configs alternate
which goes first from one request to the next. Measured: Enter -> first tool call (the moment the loop
starts running it), Enter -> the final answer, the first tool called, the reasoning tokens written ahead,
whether the notes were used, whether the answer is right (string or number match).

The engine: the agent's own llama-server, started here the way `mafdet agent --model M` starts it (one slot,
the whole context, `--jinja`), unless --url names one already running. A llama-server of your own needs
llama.cpp's raw routes (`/completion`, `/apply-template`, `/tokenize`: any recent llama-server), `--jinja`,
a reasoning template (Qwen3) and 16k of context; one slot is enough (the notes and the turn use slot 0).

    python research/agent_ahead.py research/data/agent_ahead_4b_2060.json --model qwen3-4b --n 4
    python research/agent_ahead.py research/data/agent_ahead_4b_2060.json --model qwen3-4b --first 4 --n 4
    python research/agent_ahead.py out.json --url http://127.0.0.1:8080 --model qwen3-4b

Each part appends to the JSON (a request already there is skipped: rerunning resumes); the summary is
rewritten from all rows after every request, and printed. Approval is "ask" with nobody to ask: reads
run, the shell and writes are declined, as in a real session where the user says no.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CPS = 8.0           # characters a second, with a person's pauses: as thinking_ahead.py types. At 12/s without
                    # pauses the short requests were over before the first chunk of notes (1.5 s after the
                    # first render: the agent's ~1,300-token prompt read, then 32 tokens; 2026-11-14)
SETTLE = 0.35       # the pause between the last key and Enter
SPLIT = re.compile(r"(?<=\d)[,.' ](?=\d{3}\b)")      # a thousands separator between digits

# The work folder: small files the requests read.
FILES = {
    "notes.txt": ("Project Falcon - status notes\n"
                  "Owner of the installer: Giulia Rossi\n"
                  "Owner of the updater: Marco Bianchi\n"
                  "Budget: 12000 euros\n"
                  "Spent so far: 8450 euros\n"
                  "Next review: 2026-12-03\n"),
    "sales.csv": "month,units,price\njanuary,120,15\nfebruary,95,15\nmarch,143,14\napril,88,16\nmay,131,15\n",
    "inventory.csv": "item,quantity,price\ncable,12,4\nswitch,3,35\nrouter,2,80\nadapter,10,6\n",
    "config.ini": ("[server]\nhost = 0.0.0.0\nport = 8443\nworkers = 4\n\n[database]\nhost = db.local\n"
                   "port = 5432\nname = falcon\n"),
    "todo.md": ("# Release 2.2\n\n- [x] freeze the API\n- [ ] update the installer\n- [x] translate the docs\n"
                "- [ ] sign the binaries\n- [ ] write the release notes\n- [x] tag the release candidate\n"),
    "CHANGELOG.md": ("# Changelog\n\n## 2.2 - unreleased\n- faster updater\n\n## 2.1 - 2026-03-14\n"
                     "- new installer\n- dark theme\n\n## 2.0 - 2025-11-02\n- first public release\n"),
    "poem.txt": ("The river keeps its counsel\nunder the winter bridge,\nthe gulls write on the water\n"
                 "what the wind forgot,\nand the town, half asleep,\nlistens.\nNobody answers.\n"),
    "logs/server-a.log": "10:01 INFO started\n10:02 INFO 14 clients\n10:05 WARN slow disk\n10:09 INFO 15 clients\n",
    "logs/server-b.log": "10:01 INFO started\n10:03 ERROR 4711 disk full on /var\n10:04 INFO retrying\n",
    "logs/server-c.log": "10:00 INFO started\n10:07 INFO 3 clients\n",
    "docs/report_2024.txt": "Yearly report 2024: the installer was rewritten.\n",
    "docs/report_2025.txt": "Yearly report 2025: two new customers.\n",
    "docs/report_2026.txt": "Yearly report 2026: the updater is late.\n",
    "docs/brief.txt": "Brief: Falcon replaces the old installer by spring.\n",
    "docs/minutes.txt": "Minutes of the meeting: nobody had questions.\n",
}

# (request, the answers that count as right, the tools that make sense first)
REQUESTS = [
    ("read notes.txt and tell me who owns the installer", ["giulia"], ["read_file"]),
    ("read notes.txt and tell me how many euros of the budget are left", ["3550"], ["read_file"]),
    ("read sales.csv and tell me the total number of units sold", ["577"], ["read_file"]),
    ("in sales.csv, which month sold the most units?", ["march"], ["read_file", "search"]),
    ("search the logs folder for ERROR and tell me which log file has it", ["server-b"], ["search"]),
    ("look in the logs folder and tell me the error code written after ERROR", ["4711"], ["search", "read_file"]),
    ("read config.ini and tell me the port the server listens on", ["8443"], ["read_file"]),
    ("read todo.md and tell me how many tasks are still open", ["3"], ["read_file"]),
    ("how many lines does poem.txt have? read it and tell me", ["7"], ["read_file"]),
    ("which file in the docs folder mentions Falcon? search and tell me its name", ["brief"], ["search"]),
    ("read inventory.csv and tell me the total value of the items, quantity times price", ["373"], ["read_file"]),
    ("read CHANGELOG.md and tell me the date of version 2.1", ["2026-03-14"], ["read_file", "search"]),
    ("list the files in the docs folder and tell me the newest yearly report", ["report_2026", "2026"], ["search"]),
]
CONFIGS = ("ahead", "plain")


def make_workdir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return root


def right(answer: str, accepted: list[str]) -> bool:
    """A word answer must appear in the reply (case ignored); a number must be one of its numbers, with
    thousands separators ignored (3,550 and 3.550 are 3550)."""
    text = SPLIT.sub("", (answer or "").lower())
    for a in accepted:
        a = a.lower()
        if re.fullmatch(r"\d+", a):
            if a in re.findall(r"\d+", text):
                return True
        elif a in text:
            return True
    return False


class Typist:
    """Types `text` at `cps`, calling `on_change(prefix)` at every key, as the message box does, with a
    person's pauses (`pauses=True`, as research/thinking_ahead.py types): 0.6-1.2 s after punctuation, and
    0.4-1.0 s after one space in ten, drawn from a generator seeded by the text, so "ahead" and "plain" wait
    exactly as long. Without pauses (a key every 83 ms at 12/s) the agent's draft is never rendered while
    typing: it waits for 0.15 s of quiet (ahead.DEBOUNCE), and the first run of this bench reasoned 0 tokens."""

    def __init__(self, cps: float = CPS, sleep=time.sleep, clock=time.perf_counter, pauses: bool = True):
        self.cps = cps
        self.sleep = sleep
        self.clock = clock
        self.pauses = pauses

    def schedule(self, text: str) -> list[float]:
        """Seconds from the start of typing at which each key falls."""
        import random
        import zlib
        rng = random.Random(zlib.crc32(text.encode("utf-8")))
        step, t, out = 1.0 / max(self.cps, 0.1), 0.0, []
        for i, ch in enumerate(text):
            t += step
            out.append(t)
            if self.pauses and i < len(text) - 1:
                if ch in ",.?;:":
                    t += rng.uniform(0.6, 1.2)
                elif ch == " " and rng.random() < 0.1:
                    t += rng.uniform(0.4, 1.0)
        return out

    def seconds(self, text: str) -> float:
        times = self.schedule(text)
        return times[-1] if times else 0.0

    def type(self, text: str, on_change=None):
        t0 = self.clock()
        for i, at in enumerate(self.schedule(text), start=1):
            if on_change is not None:
                on_change(text[:i])
            # keep to the clock: a slow on_change does not slow the typing down
            ahead_by = t0 + at - self.clock()
            if ahead_by > 0:
                self.sleep(ahead_by)


class Bench:
    """One request, one config: a fresh agent on the work folder, typed, then Enter.

    `plain_model` is the agent's model (ChatModel); `ahead` an `ahead.Ahead` on the same engine, or None
    (then only "plain" runs). `toolbox_for(stop, shell)`, `make_session(n)`: hooks for the tests."""

    def __init__(self, plain_model, ahead, workdir: Path, *, ctx: int = 16384, max_tokens: int = 4096,
                 max_steps: int = 12, temperature: float = 0.6, cps: float = CPS, settle: float = SETTLE,
                 scratch: Path | None = None, sleep=time.sleep):
        self.plain_model = plain_model
        self.ahead = ahead
        self.workdir = Path(workdir)
        self.ctx = ctx
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.temperature = temperature
        self.typist = Typist(cps, sleep)
        self.settle = settle
        self.sleep = sleep
        self.scratch = Path(scratch or tempfile.mkdtemp(prefix="agent-ahead-"))
        self.n = 0

    def _agent(self, model, events: list, stop: threading.Event):
        from mafdet.agent import system_prompt
        from mafdet.agent.loop import Agent
        from mafdet.agent.mcp import Toolbox
        from mafdet.agent.memory import Memory, memory_tools
        from mafdet.agent.permissions import Permissions
        from mafdet.agent.shell import Shell
        from mafdet.agent.store import Session
        from mafdet.agent.tools import builtin_tools
        self.n += 1
        memory = Memory()
        shell = Shell(self.workdir, self.scratch / f"shell-{self.n}", stop)
        box = Toolbox(builtin_tools(self.workdir, stop=stop, shell=shell) + memory_tools(memory))
        t_enter = [None]

        def emit(kind, **data):
            events.append((time.perf_counter() - (t_enter[0] or time.perf_counter()), kind, data))

        agent = Agent(model, box, Permissions("ask", None), Session(self.scratch / f"session-{self.n}"),
                      system=system_prompt(self.workdir), ctx=self.ctx, max_tokens=self.max_tokens,
                      max_steps=self.max_steps, temperature=self.temperature, think=True, emit=emit,
                      stop=stop, memory=memory)
        return agent, shell, t_enter

    def run(self, request: tuple, config: str) -> dict:
        from mafdet.agent.ahead import AheadModel
        text, accepted, first_ok = request
        if config == "ahead" and self.ahead is None:
            raise ValueError("no thinking ahead on this engine")
        model = AheadModel(self.plain_model, self.ahead) if config == "ahead" else self.plain_model
        events: list = []
        stop = threading.Event()
        agent, shell, t_enter = self._agent(model, events, stop)
        used0 = self.ahead.used if self.ahead is not None else 0
        try:
            if config == "ahead":
                self.ahead.agent = agent
                self.ahead.paused = False
                self.typist.type(text, self.ahead.typed)
                self.sleep(self.settle)
                reasoned = self.ahead.reasoned
                self.ahead.paused = True                    # as the interface does on Enter
            else:
                if self.ahead is not None:
                    self.ahead.paused = True                # nothing reasons while the plain request is typed
                self.sleep(self.typist.seconds(text) + self.settle)
                reasoned = 0
            t0 = time.perf_counter()
            t_enter[0] = t0
            error = ""
            try:
                answer = agent.run(text)
            except Exception as e:                          # a run that breaks is a row, not the end of the part
                answer, error = "", f"{type(e).__name__}: {e}"
            total = time.perf_counter() - t0
        finally:
            if self.ahead is not None:
                self.ahead.paused = False
            stop.set()
            try:
                shell.close()
            except Exception:
                pass
        tools = [d.get("name") for _, kind, d in events if kind == "tool"]
        first_tool_s = next((t for t, kind, _ in events if kind == "tool"), None)
        steps = [d for _, kind, d in events if kind == "step"]
        errors = [d.get("text") for _, kind, d in events if kind == "error"]
        return {"request": text, "config": config,
                "first_tool_s": round(first_tool_s, 3) if first_tool_s is not None else None,
                "answer_s": round(total, 3),
                "first_tool": tools[0] if tools else None,
                "first_tool_expected": bool(tools) and tools[0] in first_ok,
                "tools": tools, "steps": len(steps),
                "completion_tokens": sum(s.get("completion_tokens") or 0 for s in steps),
                "first_step_s": round(steps[0]["seconds"], 3) if steps else None,
                "reasoned_ahead": reasoned,
                "notes_used": (self.ahead.used - used0) > 0 if (config == "ahead" and self.ahead is not None) else False,
                "answer": answer, "right": right(answer, accepted), "errors": errors + ([error] if error else [])}


def _stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3)}


def summarize(rows: list[dict]) -> dict:
    """Per config: first tool and answer times, right answers, reasoning ahead; paired (ahead minus plain,
    the requests run both ways): the time differences and how often the first tool changed."""
    out: dict = {}
    for c in CONFIGS:
        rs = [r for r in rows if r["config"] == c]
        if not rs:
            continue
        out[c] = {"runs": len(rs), "right": sum(r["right"] for r in rs),
                  "first_tool_s": _stats([r["first_tool_s"] for r in rs]),
                  "answer_s": _stats([r["answer_s"] for r in rs]),
                  "first_tool_expected": sum(r["first_tool_expected"] for r in rs),
                  "no_tool": sum(1 for r in rs if r["first_tool"] is None)}
        if c == "ahead":
            out[c]["notes_used"] = sum(r["notes_used"] for r in rs)
            out[c]["reasoned_ahead"] = _stats([r["reasoned_ahead"] for r in rs])
    by = {}
    for r in rows:
        by.setdefault(r["request"], {})[r["config"]] = r
    pairs = [(p["ahead"], p["plain"]) for p in by.values() if "ahead" in p and "plain" in p]
    if pairs:
        d_first = [a["first_tool_s"] - b["first_tool_s"] for a, b in pairs
                   if a["first_tool_s"] is not None and b["first_tool_s"] is not None]
        d_answer = [a["answer_s"] - b["answer_s"] for a, b in pairs]
        out["paired"] = {"requests": len(pairs),
                         "first_tool_s_diff": _stats(d_first),
                         "answer_s_diff": _stats(d_answer),
                         "ahead_faster_first_tool": sum(1 for d in d_first if d < 0),
                         "first_tool_changed": sum(1 for a, b in pairs if a["first_tool"] != b["first_tool"]),
                         "right_both": sum(1 for a, b in pairs if a["right"] and b["right"]),
                         "right_only_ahead": sum(1 for a, b in pairs if a["right"] and not b["right"]),
                         "right_only_plain": sum(1 for a, b in pairs if b["right"] and not a["right"])}
    return out


def table(summary: dict) -> str:
    lines = [f"{'config':<7} {'runs':>4} {'right':>5} {'first tool (mean / median s)':>30} "
             f"{'answer (mean / median s)':>26} {'notes used':>10}"]
    for c in CONFIGS:
        s = summary.get(c)
        if not s:
            continue
        ft, an = s["first_tool_s"], s["answer_s"]
        lines.append(f"{c:<7} {s['runs']:>4} {s['right']:>5} {ft.get('mean', '-'):>18} / {ft.get('median', '-'):<9} "
                     f"{an.get('mean', '-'):>14} / {an.get('median', '-'):<9} {s.get('notes_used', '-'):>10}")
    p = summary.get("paired")
    if p:
        lines.append(f"paired over {p['requests']} requests, ahead minus plain: first tool "
                     f"{p['first_tool_s_diff'].get('mean')} s (median {p['first_tool_s_diff'].get('median')}), "
                     f"answer {p['answer_s_diff'].get('mean')} s (median {p['answer_s_diff'].get('median')}); "
                     f"ahead first to a tool in {p['ahead_faster_first_tool']}; first tool changed in "
                     f"{p['first_tool_changed']}; right both {p['right_both']}, only ahead "
                     f"{p['right_only_ahead']}, only plain {p['right_only_plain']}")
    return "\n".join(lines)


def load(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"rows": []}


def save(path: Path, data: dict):
    data["summary"] = summarize(data["rows"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def run_part(bench: Bench, out: Path, first: int = 0, n: int = 0, setup: dict | None = None,
             say=print) -> dict:
    """Requests first..first+n (all with n 0) both ways, appended to `out`; a request with both configs
    already there is skipped, one with only one of them gets the other."""
    data = load(out)
    if setup:
        data.setdefault("setup", setup)
    todo = list(enumerate(REQUESTS))[first: first + n if n else None]
    for i, req in todo:
        done = {r["config"] for r in data["rows"] if r["index"] == i}
        order = CONFIGS if i % 2 == 0 else CONFIGS[::-1]       # which goes first alternates
        for config in order:
            if config in done:
                continue
            row = bench.run(req, config)
            row["index"] = i
            row["when"] = time.strftime("%Y-%m-%d %H:%M:%S")
            data["rows"].append(row)
            save(out, data)
            say(f"[{i}] {config:<5} first tool {row['first_tool_s']} s ({row['first_tool']}), answer "
                f"{row['answer_s']} s, right {row['right']}, ahead {row['reasoned_ahead']} tok"
                + (f", errors {row['errors']}" if row["errors"] else ""))
    data = load(out)
    say(table(data.get("summary") or summarize(data["rows"])))
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="the JSON file: appended to, a rerun resumes")
    ap.add_argument("--model", default="qwen3-4b", help="a model id of `mafdet models`, or the alias at --url")
    ap.add_argument("--url", default="", help="a llama-server already running (default: start the agent's own)")
    ap.add_argument("--ctx", type=int, default=16384)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--temperature", type=float, default=0.6, help="the agent's default")
    ap.add_argument("--cps", type=float, default=CPS, help="typing speed, characters a second")
    ap.add_argument("--first", type=int, default=0, help="first request (0-%d)" % (len(REQUESTS) - 1))
    ap.add_argument("--n", type=int, default=0, help="requests in this part (0: to the end)")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--engine-port", type=int, default=8091, help="the own llama-server's port (default: %(default)s)")
    ap.add_argument("--llama-args", default="")
    args = ap.parse_args(argv)

    from mafdet.agent.ahead import Ahead
    from mafdet.agent.model import ChatModel
    engine = None
    if args.url:
        url, alias = args.url.rstrip("/"), args.model
    else:
        from mafdet.agent import start_engine
        ns = argparse.Namespace(model=args.model, device=args.device, ctx=args.ctx, engine_port=args.engine_port,
                                llama_args=args.llama_args)
        engine, alias = start_engine(ns)
        url = engine.url
    work = Path(tempfile.mkdtemp(prefix="agent-ahead-work-"))
    ahead = None
    try:
        workdir = make_workdir(work / "project")
        ahead = Ahead.for_engine(url, args.ctx)
        if ahead is None:
            raise SystemExit(f"no thinking ahead on {url}: not llama-server, or no reasoning template")
        bench = Bench(ChatModel(url, alias), ahead, workdir, ctx=args.ctx, max_tokens=args.max_tokens,
                      max_steps=args.max_steps, temperature=args.temperature, cps=args.cps, scratch=work / "scratch")
        setup = {"model": alias, "url": "own llama-server" if engine else url, "ctx": args.ctx, "cps": args.cps,
                 "settle": SETTLE, "temperature": args.temperature, "max_tokens": args.max_tokens,
                 "requests": len(REQUESTS)}
        run_part(bench, Path(args.out), args.first, args.n, setup, say=lambda s: print(s, flush=True))
    finally:
        if ahead is not None:
            ahead.close()
        if engine is not None:
            engine.close()
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
