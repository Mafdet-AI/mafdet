"""`mafdet agent --walk <binary> --mcp <server>`: every function of a binary named and summarized by a
local model, bottom-up over the call graph, unattended, into the book (`book.py`).

The walk is driven by code, not by the model:
  1. the binary is opened through the server (`backend.py` says how each server spells it), its
     functions listed, the callees of each fetched once and kept in the book;
  2. the functions are ordered leaves first: strongly connected components (Tarjan, iterative) in
     reverse topological order; inside a cycle, by address, members seeing the peers not named yet
     under their decompiler's names;
  3. functions that already carry a real name (imports, exports, symbols: not `sub_...`, see
     backend.DEFAULT_NAMES) are known, and not sent to the model;
  4. each other function gets a FRESH short conversation: the same system prompt and the same one
     tool every time (so llama-server's prompt cache keeps that prefix), then its pseudo-code with the
     callees already named written under their new names, and the list of its callees with their
     one-line summaries. The model answers by calling `record_function`. A small context never holds
     more than one function: what was learned of the callees comes back as names and summaries;
  5. the answer is checked (a C identifier, unique in the book, a one-line summary); a bad answer
     is told back once, then the function is marked failed and the walk goes on;
  6. when the server can rename and comment and the permissions allow it (as for a call the model
     made; nobody is asked, a call that would ask is skipped), the name and summary are written back;
     a failure there is noted in the book and never stops the walk.

The book is saved after every function: rerunning the same command goes on where it stopped.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

from .backend import DEFAULT_IN_TEXT, Adapter, BackendError, is_default
from .book import Book, has_code, now as book_now, sort_key
from .context import clip
from .model import ModelError, Stopped

WALK_SYSTEM = """You name the functions of a compiled program, one function at a time, for a reverse engineer.

You get one function's decompiled pseudo-code. The functions it calls were analysed before it: they appear under the names given to them, and the list after the code says what each one does. A name still in the decompiler's form (sub_..., FUN_...) belongs to a function not understood yet.

Answer only by calling record_function, once:
- name: a C identifier in snake_case that says what the function does (parse_config_header, xor_decrypt_buffer, send_beacon), not how; no address in it.
- summary: one line: what it does, with what inputs, what it returns, what it changes.
- comment: optional, a few lines on what is not obvious (a constant, an algorithm, a data format).
- confidence: high when the code says it plainly, medium for a reasonable reading, low for a guess.
Read the code; do not invent what it does not show."""

RECORD = {"type": "function", "function": {
    "name": "record_function",
    "description": "Record the name and the summary of the function shown.",
    "parameters": {"type": "object", "required": ["name", "summary", "confidence"], "properties": {
        "name": {"type": "string", "description": "a C identifier, snake_case"},
        "summary": {"type": "string", "description": "one line"},
        "comment": {"type": "string", "description": "optional, a few lines"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}}}

NO_CALL = ("You did not call record_function. Answer only with a call to record_function(name, summary, "
           "confidence), no other text.")
C_KEYWORDS = {"auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum",
              "extern", "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return",
              "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void",
              "volatile", "while", "bool", "true", "false", "main_function", "function", "func", "unknown"}
NAME_MIN, NAME_MAX = 3, 60
SUMMARY_CHARS = 300
COMMENT_CHARS = 2000
CALLEES_SHOWN = 60          # a dispatcher calling hundreds: the first ones, and a count
FIXED_TOKENS = 700          # system prompt, tool, the message's frame
CODE_CHARS_PER_TOKEN = 2.8  # pseudo-code tokenizes worse than prose
SAVE_EVERY = 25             # callees fetched between saves; book.md every this many functions
# The walk's context when --ctx is not given: one function at a time needs little (where.exe's prompts were
# 500-1,100 tokens, plus up to --max-tokens of reasoning; at 16k `code_budget` still shows ~32,000 characters
# of pseudo-code). The agent's choice (the largest of 32k/24k/16k with 1 GB of VRAM left) would give a larger
# card 32k, for nothing here. It was 24k on the RTX 4070 SUPER before it counted the desktop's memory: with
# Discord encoding video, the 14B went from 40 to 5-27 tok/s (Windows paging its memory) and stayed at 39-47
# at 16k and 8k (2026-11-12).
WALK_CTX = 16384


def bottom_up(nodes: list[str], edges: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components, callees before callers (Tarjan's order), without recursion:
    a binary's call graph can be thousands deep. Each component is sorted by address."""
    known = set(nodes)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(edges.get(root) or ()))]
        while work:
            v, it = work[-1]
            descended = False
            for w in it:
                if w not in known:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(edges.get(w) or ())))
                    descended = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp, key=sort_key))
    return out


def check(call: dict) -> tuple[dict | None, str]:
    """A record_function call -> (the record, "") or (None, what is wrong with it)."""
    if call.get("name") != "record_function":
        return None, f"there is no tool {call.get('name')!r}: the only tool is record_function"
    try:
        args = json.loads(call.get("arguments") or "{}")
        if not isinstance(args, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return None, f"the arguments are not a JSON object ({e})"
    name = str(args.get("name") or "").strip().strip("`'\"").removesuffix("()").strip()
    if not name:
        return None, "name is missing"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None, f"name {name!r} is not a C identifier (letters, digits, underscores; not starting with a digit)"
    if not NAME_MIN <= len(name) <= NAME_MAX:
        return None, f"name {name!r} must be {NAME_MIN} to {NAME_MAX} characters"
    if name in C_KEYWORDS or is_default(name):
        return None, f"name {name!r} says nothing about the function: give a descriptive one"
    summary = " ".join(str(args.get("summary") or "").split())
    if not summary:
        return None, "summary is missing"
    if len(summary) > SUMMARY_CHARS:
        summary = summary[:SUMMARY_CHARS - 1].rsplit(" ", 1)[0] + "…"
    confidence = str(args.get("confidence") or "").strip().lower()
    confidence = confidence if confidence in ("low", "medium", "high") else "low"
    comment = str(args.get("comment") or "").strip()[:COMMENT_CHARS]
    return {"name": name, "summary": summary, "comment": comment, "confidence": confidence}, ""


class Walk:
    def __init__(self, book: Book, server, model, *, permissions=None, allow=(), out=None, ctx: int = 16384,
                 max_tokens: int = 4096, temperature: float = 0.6, think: bool = True,
                 stop: threading.Event | None = None, max_functions: int = 0, time_limit: float = 0.0,
                 retry_failed: bool = False, ready_timeout: float = 1800.0, model_label: str = "",
                 poll: float = 5.0):
        from .permissions import Permissions
        self.book = book
        self.server = server
        self.model = model
        self.permissions = permissions or Permissions("ask")
        self.allow = list(allow or ())
        self.out = out or sys.stdout
        self.ctx = ctx
        self.max_tokens = max_tokens
        self.reserve = min(max_tokens, ctx // 3)
        self.temperature = temperature
        self.think = think
        self.stop = stop or threading.Event()
        self.max_functions = max_functions
        self.time_limit = time_limit                    # seconds, 0 for none
        self.retry_failed = retry_failed
        self.ready_timeout = ready_timeout
        self.poll = poll                                # seconds between looks at an import in progress
        self.model_label = model_label
        self.adapter: Adapter | None = None
        self.mirror: dict[str, str] = {}                # op -> "" (written) or why not

    def say(self, text: str):
        self.out.write(text + "\n")
        self.out.flush()

    # ------------------------------------------------------------ the run

    def run(self, binary: Path) -> dict:
        t0 = time.monotonic()
        book = self.book
        run = {"started": book_now(), "model": self.model_label, "done": 0, "failed": 0, "skipped": 0,
               "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "stopped": ""}
        book.data["runs"].append(run)
        try:
            self.adapter = adapter = Adapter.connect(self.server, binary)
            self.say(f"walk  {binary}\n  book     {book.dir}\n"
                     f"  server   {getattr(self.server, 'name', '?')}, read as {adapter.name} ({adapter.table['source']})")
            adapter.open(say=lambda t: self.say("  " + t), stop=self.stop, ready_timeout=self.ready_timeout,
                         poll=self.poll)
            self.say(f"  can      {adapter.operations()}")       # once open: what this host can run is known
            self._plan_mirror()
            book.data["backend"] = {"server": getattr(self.server, "name", "?"), "adapter": adapter.name,
                                    "operations": [op for op in ("list", "callees", "code", "rename", "comment")
                                                   if adapter.has(op)]}
            t_list = time.monotonic()
            listed = adapter.functions()
            if time.monotonic() - t_list > 10:
                self.say(f"  listed in {time.monotonic() - t_list:.0f} s (the decompiler's analysis included)")
            if not listed and not book.functions:
                raise BackendError(f"{adapter.table['list']['tool']} listed no function")
            new = book.merge(listed, lambda name: bool(name) and not is_default(name))
            c = book.counts()
            self.say(f"  functions {len(book.functions)} ({new} new): {c['known']} with a real name, "
                     f"{c['done']} done before, {c['pending']} pending, {c['failed']} failed")
            book.save()
            self._callees()
            order = [a for comp in bottom_up(sorted(book.functions, key=sort_key),
                                             {a: f.get("callees") or [] for a, f in book.functions.items()})
                     for a in comp]
            todo = [a for a in order if book.functions[a]["status"] == "pending"
                    or self.retry_failed and book.functions[a]["status"] == "failed"]
            if self.max_functions:
                todo = todo[:self.max_functions]
            self.say(f"  to do    {len(todo)} functions, leaves first\n")
            self._functions(todo, run, t0)
        except KeyboardInterrupt:
            run["stopped"] = "interrupted"
        except (BackendError, OSError) as e:
            run["stopped"] = f"backend: {e}"
            self.say(f"! {e}")
        except Exception as e:                          # McpError and the like: the book stays as it is
            run["stopped"] = f"{type(e).__name__}: {e}"
            self.say(f"! {type(e).__name__}: {e}")
        finally:
            self._save_backend()
            run["ended"] = book_now()
            run["seconds"] = round(time.monotonic() - t0, 1)
            book.save(md=True)
        self._summary(run)
        return run

    def _plan_mirror(self):
        """Which writes to the decompiler will happen, decided once, as for a call the model made."""
        from . import mcp
        a = self.adapter
        for op in ("rename", "comment"):
            if not a.has(op):
                self.mirror[op] = a.why_not(op) or "the server has no such tool"
                continue
            tool = mcp.as_tool(self.server, a.specs[a.table[op]["tool"]], allow=self.allow)
            probe = a._args(op, {"address": "0x0", "name": "x", "comment": "x"})
            if self.permissions.allowed(tool, probe):
                self.mirror[op] = ""
            else:
                self.mirror[op] = (f"{tool.name} would ask, and nobody is asked in a walk: add it to `allow` "
                                   "in mcp.json, or --approve all")
        for op, why in self.mirror.items():
            self.say(f"  {op:<8} " + ("written back to the decompiler" if not why else f"not written back: {why}"))

    def _callees(self):
        book, a = self.book, self.adapter
        # A function with a real name is not sent to the model, but its callees are part of the graph:
        # `entry` (the PE's entry point, a real name) calls the CRT's startup, which calls main. Without its
        # edges the tree of book.md began at whatever unnamed function had the lowest address (where.exe,
        # 2026-11-12). Imports and thunks have no code of their own: not asked.
        missing = [x for x in sorted(book.functions, key=sort_key)
                   if book.functions[x]["callees"] is None and has_code(book.functions[x])]
        if missing:
            self.say(f"  callees of {len(missing)} functions...")
        errors = 0
        for i, addr in enumerate(missing, 1):
            if self.stop.is_set():
                raise KeyboardInterrupt
            f = book.functions[addr]
            try:
                said = a.callees(addr)
                errors = 0
            except BackendError as e:
                errors += 1
                f["flags"]["callees_error"] = str(e)[:200]
                said = []
                if errors >= 5:
                    raise BackendError(f"five failures in a row fetching callees, the last: {e}")
            inside, outside = [], []
            for ref in said:
                target = book.resolve(ref)
                if target is None:
                    if ref not in outside:
                        outside.append(ref)
                elif target not in inside:
                    inside.append(target)
            f["callees"], f["outside"] = inside, outside[:30]
            if i % SAVE_EVERY == 0:
                book.save()
            if i % 200 == 0:
                self.say(f"  callees  {i}/{len(missing)}")
        book.save()

    def _functions(self, todo: list[str], run: dict, t0: float):
        book = self.book
        model_errors = backend_errors = 0
        for i, addr in enumerate(todo, 1):
            if self.stop.is_set():
                run["stopped"] = "stopped"
                return
            if self.time_limit and time.monotonic() - t0 > self.time_limit:
                run["stopped"] = "time limit"
                self.say(f"  time limit reached: {len(todo) - i + 1} functions left for the next run")
                return
            f = book.functions[addr]
            try:
                result = self._one(f)
            except Stopped:
                run["stopped"] = "stopped"
                return
            status = f["status"]
            run[status] = run.get(status, 0) + 1
            for k in ("prompt_tokens", "completion_tokens"):
                run[k] += result.get(k, 0)
            model_errors = model_errors + 1 if result.get("model_error") else 0
            backend_errors = backend_errors + 1 if result.get("backend_error") else 0
            self.say(self._progress(i, len(todo), f, result))
            # A kill keeps the run's record up to the last function (it said 0 s after one, 2026-11-12).
            run["seconds"], run["ended"] = round(time.monotonic() - t0, 1), book_now()
            book.save(md=i % SAVE_EVERY == 0)
            if i % SAVE_EVERY == 0:
                self._save_backend()
            if model_errors >= 3:
                run["stopped"] = "the engine failed three times in a row"
                self.say("! the engine failed three times in a row: stopping (rerun to go on)")
                return
            if backend_errors >= 5:
                run["stopped"] = "the server failed five times in a row"
                self.say("! the server failed five times in a row: stopping (rerun to go on)")
                return

    def _save_backend(self):
        """The names and comments written back, onto the decompiler's disk (a project server keeps them
        in memory until saved)."""
        if self.adapter is not None:
            why = self.adapter.save()
            if why:
                self.say(f"  the decompiler did not save its project: {why}")

    def _progress(self, i: int, n: int, f: dict, r: dict) -> str:
        head = f"[{i}/{n}] {f['address']} {f['orig']}"
        tokens = f"{r.get('prompt_tokens', 0)}+{r.get('completion_tokens', 0)} tok"
        secs = f"{r.get('seconds', 0.0):.1f} s"
        if f["status"] == "done":
            tries = f", {f['steps']} tries" if f["steps"] > 1 else ""
            mirror = r.get("mirror", "")
            return f"{head} -> {f['name']}  [{f['confidence']}]  {secs}  {tokens}{tries}{mirror}"
        return f"{head} {f['status']}: {f['reason']}  {secs}  {tokens}"

    # ------------------------------------------------------------ one function

    def callee_text(self, f: dict) -> str:
        """What the model reads after the code: each callee as `name — one-line summary`."""
        fs = self.book.functions
        lines = []
        callees = f.get("callees") or []
        for c in callees[:CALLEES_SHOWN]:
            g = fs[c]
            if c == f["address"]:
                lines.append(f"- {g['orig']} — this function itself (it is recursive)")
            elif g["status"] == "done":
                lines.append(f"- {g['name']} — {g['summary']}")
            elif g["status"] == "known":
                lines.append(f"- {g['orig']} — " + ("imported" if g["flags"].get("external") else
                                                    "a thunk (a jump to the function of that name)"
                                                    if g["flags"].get("thunk") else "named in the binary"))
            else:
                lines.append(f"- {g['orig']} — not named yet")
        if len(callees) > CALLEES_SHOWN:
            lines.append(f"- … and {len(callees) - CALLEES_SHOWN} more")
        text = "It calls:\n" + "\n".join(lines) if lines else "It calls no other function of the program."
        if f.get("outside"):
            text += "\nIt also calls (imports, or not resolved): " + ", ".join(f["outside"][:30])
        return text

    def rename_in(self, code: str) -> str:
        """Default names in the code of functions already named -> their given names."""
        book = self.book

        def sub(m):
            a = book.resolve(m.group(0))
            g = book.functions.get(a) if a else None
            return g["name"] if g and g["status"] == "done" and g["name"] else m.group(0)
        return DEFAULT_IN_TEXT.sub(sub, code)

    def code_budget(self, callee_text: str) -> int:
        tokens = self.ctx - self.reserve - FIXED_TOKENS
        return max(1500, int(tokens * CODE_CHARS_PER_TOKEN) - len(callee_text) - 300)

    def _one(self, f: dict) -> dict:
        """Analyse one function; the book entry is changed only when it ends (a stop leaves it as it was)."""
        t0 = time.perf_counter()
        r = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

        def end(status: str, reason: str = "", steps: int = 0):
            r["seconds"] = time.perf_counter() - t0
            f.update(status=status, reason=reason, seconds=round(r["seconds"], 2), steps=steps,
                     prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"], updated=book_now())
            return r

        try:
            code = self.adapter.code(f["address"])
        except BackendError as e:
            r["backend_error"] = True
            return end("failed", f"backend: {str(e)[:200]}")
        if not code.strip():
            return end("skipped", "the decompiler gave no pseudo-code")
        f["code_chars"] = len(code)
        callee_text = self.callee_text(f)
        budget = self.code_budget(callee_text)
        body = clip(self.rename_in(code).strip("\n"), budget)
        size = f" ({int(f['size'])} bytes)" if f.get("size") else ""
        user = (f"Function {f['orig']} at {f['address']}{size}:\n\n```c\n{body}\n```\n\n{callee_text}\n\n"
                "Call record_function for this function.")
        messages = [{"role": "system", "content": WALK_SYSTEM}, {"role": "user", "content": user}]
        error = ""
        for step in (1, 2):
            try:
                reply = self.model.chat(messages, [RECORD], max_tokens=self.max_tokens, temperature=self.temperature,
                                        think=self.think, should_stop=self.stop.is_set)
            except ModelError as e:
                r["model_error"] = True
                # An engine gone is not the function's fault: it stays pending for the next run. An
                # answer of the engine against this request (a prompt too long) is.
                gone = "does not answer" in str(e)
                return end("pending" if gone else "failed", f"engine: {str(e)[:200]}", step)
            r["prompt_tokens"] += reply.prompt_tokens
            r["completion_tokens"] += reply.completion_tokens
            record = None
            for call in reply.tool_calls:
                record, error = check(call)
                if record:
                    break
            if record:
                end("done", "", step)
                self.book.record(f["address"], **record)
                r["mirror"] = self._write_back(f)
                return r
            if not reply.tool_calls:
                error = "no record_function call" + (f" (cut at {self.max_tokens} tokens)"
                                                     if reply.finish == "length" else "")
            messages.append(reply.message())
            if reply.tool_calls:
                for call in reply.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": f"error: {error}. Call record_function again, correctly."})
            else:
                messages.append({"role": "user", "content": NO_CALL})
        return end("failed", error, 2)

    def _write_back(self, f: dict) -> str:
        """The name and the summary into the decompiler's database, when allowed. Returns a note for the
        progress line; what happened is in the book."""
        notes, a = [], self.adapter
        values = {"address": f["address"], "name": f["name"],
                  "comment": f["summary"] + (f"\n\n{f['comment']}" if f.get("comment") else "")}
        for op in ("rename", "comment"):
            if self.mirror.get(op, "x"):
                continue
            why = a.write(op, **values)
            f["mirror"][op] = "written" if not why else f"failed: {why}"
            if why:
                notes.append(f"{op} failed")
        return ("  (" + ", ".join(notes) + ")") if notes else ""

    def _summary(self, run: dict):
        c = self.book.counts()
        analysed = run.get("done", 0) + run.get("failed", 0)
        mean = sum(f["seconds"] for f in self.book.functions.values()
                   if f["status"] in ("done", "failed")) / max(1, c["done"] + c["failed"])
        self.say(f"\nthis run: {run.get('done', 0)} named, {run.get('failed', 0)} failed, {run.get('skipped', 0)} "
                 f"skipped in {run['seconds']:.0f} s, {run['prompt_tokens']}+{run['completion_tokens']} tokens"
                 + (f", {run['seconds'] / analysed:.1f} s per function" if analysed else "")
                 + (f"; stopped: {run['stopped']}" if run.get("stopped") else ""))
        self.say(f"the book: {len(self.book.functions)} functions, {c['done']} done, {c['known']} known, "
                 f"{c['skipped']} skipped, {c['failed']} failed, {c['pending']} pending; mean {mean:.1f} s per "
                 f"function\n  {self.book.dir / 'book.md'}")


# ---------------------------------------------------------------- the command

def run(args) -> int:
    """`mafdet agent --walk <binary> --mcp <server>`."""
    from . import engine_ctx, settings, start_engine
    from . import mcp
    from .model import ChatModel
    from .permissions import Permissions
    from .store import sessions_dir

    settings(args)
    binary = Path(args.walk).expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(f"{binary} is not a file.")
    names = args.mcp or []
    if not names:
        raise SystemExit("--walk needs the decompiler's MCP server: --mcp <name> (from ~/.mafdet/mcp.json).")
    name = names[0]
    if len(names) > 1:
        print(f"the walk uses one server: {name} (not {', '.join(names[1:])})", file=sys.stderr)
    conf = mcp.configured().get(name)
    if not isinstance(conf, dict) or not conf.get("command"):
        from ..core import config
        raise SystemExit(f"No MCP server named {name} in {config.home() / 'mcp.json'}.")
    book = Book.for_binary(binary)
    stop = threading.Event()
    print(f"MCP server {name}: starting...", file=sys.stderr, flush=True)
    server = mcp.McpServer(name, conf["command"], conf.get("args") or [], conf.get("env"), conf.get("cwd"),
                           log=mcp.log_path(sessions_dir(), name))
    engine = walk = None
    try:
        if not args.ctx and not args.url:
            args.ctx = WALK_CTX
        if args.url:
            args.ctx = args.ctx or engine_ctx(args.url)
            model = ChatModel(args.url, args.model or "default", llama=args.engine == "llama", api_key=args.api_key)
            label_ = f"{model.model} at {args.url}"
        else:
            engine, alias = start_engine(args)
            model = ChatModel(engine.url, alias)
            label_ = alias
        walk = Walk(book, server, model, permissions=Permissions(args.approve, None),
                    allow=[str(p) for p in conf.get("allow") or []], ctx=args.ctx, max_tokens=args.max_tokens,
                    temperature=args.temperature, think=args.think, stop=stop,
                    max_functions=args.max_functions or 0, time_limit=(args.time_limit or 0) * 60,
                    retry_failed=args.retry_failed, model_label=label_)
        result = walk.run(binary)
        return 0 if not result.get("stopped") or result["stopped"] in ("time limit", "interrupted", "stopped") else 1
    finally:
        stop.set()
        if walk is not None and walk.adapter is not None:
            walk.adapter.close()
        server.close()
        if engine is not None:
            engine.close()
