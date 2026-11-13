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

The second pass (`--revise`), once the first is complete: a function at the top was named knowing only
what is below it (main became `validate_config_and_initialize` in where.exe, 2026-11-12). So:
  7. the program's summary first: one conversation with one tool, `record_program`, from the file's
     name, its imports by library, the top of the call tree as the first pass named it, and some of its
     strings when the server lists them (an optional operation of `backend.py`);
  8. then every function the first pass named, from the roots down (the reverse of the first pass's
     order; a cycle by address), each in a fresh conversation with its own fixed system prompt and the
     same tool: the program's summary, its callers (reviewed already, so a correction flows down), its
     pseudo-code and callees with the current names, and its first name and summary as a draft to keep
     unless wrong. The book keeps the first pass's record next to the second's (`book.revise`); a
     changed name or summary is written back as in step 6. `--revise-depth`, `--revise-skip-high` and
     `--max-functions` (counted over both passes) limit it; `--revise-no-program` and
     `--revise-no-callers` leave a piece out of its prompt, to measure what each brings;
     `--revise-no-draft` (an experiment) hides the first reading from it: no first name, summary or
     confidence, and a plain request to name the function (REVISE_NODRAFT_SYSTEM), so what the callers and
     the program bring is not hidden behind a draft kept out of inertia;
     `--revise-bottom-up` (an experiment) goes in the first pass's order instead, callees first, so a
     correction travels upward as the mistakes did; `--revise-call-lines` (an experiment) shows each caller
     with up to CALL_LINES lines of its code that call the function, as upstream's --refine does.
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

# The second pass (--revise): the same tool, its own system prompt, the same for every function of the pass
# and for every variant (--revise-no-program, --revise-no-callers change only the user's message), so the
# prefix stays cached and a variant's effect is its message's.
REVISE_SYSTEM = """You review the names given to the functions of a compiled program, for a reverse engineer.

A first reading named every function from the bottom up: each one was named seeing only its own pseudo-code and the functions it calls, never what calls it or what the program is. Those names are mostly right at the bottom of the call tree and often wrong near the top, where a word read in one function (config, buffer, handle) travels up into the names of its callers.

Now the functions come again from the top down, one at a time. You get what the program is and the functions that call this one, when they are known (the callers were reviewed already), its pseudo-code with the names given so far, the functions it calls, and the first reading's name and summary as a draft.

Answer only by calling record_function, once:
- name: keep the draft's name when it is right. Change it only when it is wrong or misleading in the light of the callers and of what the program does, not for style or for a synonym. A new name is a C identifier in snake_case that says what the function does, not how; no address in it.
- summary: one line: what it does, with what inputs, what it returns, what it changes; the draft's, corrected where it is wrong.
- comment: optional, a few lines on what is not obvious (a constant, an algorithm, a data format).
- confidence: high when the code says it plainly, medium for a reasonable reading, low for a guess.
The callers say what the function is for; the code says what it does: do not invent what the code does not show."""

# --revise-no-draft: the second pass without the first reading's name and summary of the function (the first
# real numbers, 2026-11-13: shown its first name, a second pass kept it 9 times in 10 though two first readings
# of where.exe agree on 11-18 names of 81). The same pieces otherwise; the callers and callees are still shown
# under the names given so far: they are context, not this function's draft.
REVISE_NODRAFT_SYSTEM = """You name the functions of a compiled program, one function at a time, for a reverse engineer, now that all of it has been read once.

A first reading named every function from the bottom up, each one seeing only its own pseudo-code and the functions it calls, never what calls it or what the program is. Its names are mostly right at the bottom of the call tree and often wrong near the top, where a word read in one function (config, buffer, handle) travels up into the names of its callers.

Now the functions come again from the top down, one at a time. You get what the program is and the functions that call this one, when they are known (the callers were read again already), its pseudo-code with the names given so far, and the functions it calls with what they do. The names of the other functions can be wrong too: trust the code over a name.

Answer only by calling record_function, once:
- name: a C identifier in snake_case that says what the function does (parse_config_header, xor_decrypt_buffer, send_beacon), not how; no address in it.
- summary: one line: what it does, with what inputs, what it returns, what it changes.
- comment: optional, a few lines on what is not obvious (a constant, an algorithm, a data format).
- confidence: high when the code says it plainly, medium for a reasonable reading, low for a guess.
The callers say what the function is for; the code says what it does: do not invent what the code does not show."""

# --revise-bottom-up and --revise-call-lines change what the second pass's system prompt says of its order and of
# its callers; built from the two above so that only those sentences differ, and checked here.
_TOP_DOWN = {
    REVISE_SYSTEM: "Now the functions come again from the top down, one at a time. You get what the program is and the functions that call this one, when they are known (the callers were reviewed already), its pseudo-code",
    REVISE_NODRAFT_SYSTEM: "Now the functions come again from the top down, one at a time. You get what the program is and the functions that call this one, when they are known (the callers were read again already), its pseudo-code"}
_BOTTOM_UP = {
    REVISE_SYSTEM: "Now the functions come again from the bottom up, in the first reading's order, one at a time: the functions this one calls were reviewed already and appear under their reviewed names and summaries; the functions that call it were not, and appear as the first reading named them (inside a cycle, some of either). You get what the program is and the functions that call this one, when they are known, its pseudo-code",
    REVISE_NODRAFT_SYSTEM: "Now the functions come again from the bottom up, in the first reading's order, one at a time: the functions this one calls were read again already and appear under their new names and summaries; the functions that call it were not, and appear as the first reading named them (inside a cycle, some of either). You get what the program is and the functions that call this one, when they are known, its pseudo-code"}
_CALL_LINES = "the functions that call this one, when they are known"


def revise_system(draft: bool = True, bottom_up: bool = False, call_lines: bool = False) -> str:
    """The second pass's system prompt: REVISE_SYSTEM or REVISE_NODRAFT_SYSTEM as they are, or with the
    sentences on the order (--revise-bottom-up) and on the callers' lines of code (--revise-call-lines) changed."""
    base = REVISE_SYSTEM if draft else REVISE_NODRAFT_SYSTEM
    text = base
    if bottom_up:
        assert _TOP_DOWN[base] in text
        text = text.replace(_TOP_DOWN[base], _BOTTOM_UP[base])
    if call_lines:
        assert text.count(_CALL_LINES) == 1
        text = text.replace(_CALL_LINES, "the functions that call this one, with the lines of their code that call it, when they are known")
    return text


for _d in (True, False):
    for _b in (True, False):
        revise_system(_d, _b, True)                     # every variant builds (an import fails otherwise)

PROGRAM_SYSTEM = """You describe a compiled program for a reverse engineer, from what a first reading of it found.

You get the file's name, the functions it imports grouped by library, the top of its call tree with the names and one-line summaries the first reading gave, and, when available, some of the strings in the binary. Each function was named seeing only the functions it calls, so the names near the top can be wrong: trust the imports and the strings over the names.

Answer only by calling record_program, once:
- summary: 2 to 4 sentences: what the program is (a command-line tool, a service, a library, an installer, a game, malware of some kind...), what it does, and how it is used (its inputs, its outputs). Say what the evidence shows; when a part is a guess, say so."""

RECORD_PROGRAM = {"type": "function", "function": {
    "name": "record_program",
    "description": "Record what the program is and what it does.",
    "parameters": {"type": "object", "required": ["summary"], "properties": {
        "summary": {"type": "string", "description": "2 to 4 sentences"}}}}}

NO_CALL_PROGRAM = ("You did not call record_program. Answer only with a call to record_program(summary), "
                   "no other text.")
PROGRAM_CHARS = 1200        # the program's summary, at most
PROGRAM_DEPTH = 6           # levels of the call tree shown for it, at most (roots are level 1)
PROGRAM_FUNCTIONS = 60      # functions of the tree shown, at most, chosen level by level
IMPORT_LIBRARIES = 12       # libraries shown, the ones with the most imports first
IMPORTS_PER_LIBRARY = 30
STRINGS_SHOWN = 40          # string literals shown, those with a space first (messages, usage lines)
STRING_CHARS = 120
CALLERS_SHOWN = 12          # callers in a function's revise prompt
CALL_LINES = 3              # --revise-call-lines: lines of each caller's code that call the function
CALL_LINE_CHARS = 200
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


def arguments(call: dict, tool: str) -> tuple[dict | None, str]:
    """A call of the one tool of a conversation -> (its arguments, "") or (None, what is wrong with it)."""
    if call.get("name") != tool:
        return None, f"there is no tool {call.get('name')!r}: the only tool is {tool}"
    try:
        args = json.loads(call.get("arguments") or "{}")
        if not isinstance(args, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return None, f"the arguments are not a JSON object ({e})"
    return args, ""


def check(call: dict) -> tuple[dict | None, str]:
    """A record_function call -> (the record, "") or (None, what is wrong with it)."""
    args, error = arguments(call, "record_function")
    if args is None:
        return None, error
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


def check_program(call: dict) -> tuple[dict | None, str]:
    """A record_program call -> ({"summary"}, "") or (None, what is wrong with it)."""
    args, error = arguments(call, "record_program")
    if args is None:
        return None, error
    summary = " ".join(str(args.get("summary") or "").split())
    if not summary:
        return None, "summary is missing"
    if len(summary) < 20:
        return None, f"summary {summary!r} is too short: 2 to 4 sentences on what the program is and does"
    if len(summary) > PROGRAM_CHARS:
        cut = summary[:PROGRAM_CHARS]
        end = cut.rfind(". ")
        summary = cut[:end + 1] if end > PROGRAM_CHARS // 2 else cut.rsplit(" ", 1)[0] + "…"
    return {"summary": summary}, ""


def top_down(components: list[list[str]]) -> list[str]:
    """The second pass's order: the reverse of the first's components (callers before callees), each
    component kept in its order (by address): a stable order inside cycles."""
    return [a for comp in reversed(components) for a in comp]


class Walk:
    def __init__(self, book: Book, server, model, *, permissions=None, allow=(), out=None, ctx: int = 16384,
                 max_tokens: int = 4096, temperature: float = 0.6, think: bool = True,
                 stop: threading.Event | None = None, max_functions: int = 0, time_limit: float = 0.0,
                 retry_failed: bool = False, ready_timeout: float = 1800.0, model_label: str = "",
                 poll: float = 5.0, revise: bool = False, revise_depth: int | None = None,
                 revise_skip_high: bool = False, revise_program: bool = True, revise_callers: bool = True,
                 revise_draft: bool = True, revise_bottom_up: bool = False, revise_call_lines: bool = False):
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
        # The second pass and its scope; `program` and `callers` are the pieces of its prompt, each
        # switchable to measure what it brings (recorded per function in `revision.with`).
        self.revise = revise
        self.revise_depth = revise_depth                # None: every function the model named
        self.revise_skip_high = revise_skip_high        # leave out what the first pass named with high confidence
        self.revise_with = {"program": revise_program, "callers": revise_callers}
        if not revise_draft:                            # recorded only when hidden: --revise's books stay as they were
            self.revise_with["draft"] = False
        if revise_bottom_up:                            # the same: recorded only when set
            self.revise_with["order"] = "bottom_up"
        if revise_call_lines:
            self.revise_with["call_lines"] = True
        self._first_order: list[str] = []               # the first pass's order (--revise-bottom-up)
        self._caller_code: dict[str, str] = {}          # a caller's pseudo-code, fetched once (--revise-call-lines)
        self._callers: dict[str, list[str]] | None = None
        self._facts_counts: dict[str, int] = {}

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
            components = bottom_up(sorted(book.functions, key=sort_key),
                                   {a: f.get("callees") or [] for a, f in book.functions.items()})
            order = [a for comp in components for a in comp]
            self._first_order = order
            todo = [a for a in order if book.functions[a]["status"] == "pending"
                    or self.retry_failed and book.functions[a]["status"] == "failed"]
            if self.max_functions:
                todo = todo[:self.max_functions]
            self.say(f"  to do    {len(todo)} functions, leaves first\n")
            self._functions(todo, run, t0)
            if self.revise and not run["stopped"]:
                self._second_pass(top_down(components), run, t0, sent=len(todo))
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

    def _functions(self, todo: list[str], run: dict, t0: float, one=None, progress=None):
        """The functions of a pass, one by one (`one` analyses one: the first pass's `_one` by default),
        the book saved after each; stops on a stop, the time limit, or errors in a row."""
        book = self.book
        one, progress = one or self._one, progress or self._progress
        model_errors = backend_errors = 0
        for i, addr in enumerate(todo, 1):
            if self.stop.is_set():
                run["stopped"] = "stopped"
                return
            if self._out_of_time(run, t0, len(todo) - i + 1):
                return
            f = book.functions[addr]
            try:
                result = one(f)
            except Stopped:
                run["stopped"] = "stopped"
                return
            status = result.get("status") or f["status"]
            run[status] = run.get(status, 0) + 1
            if result.get("renamed"):
                run["renamed"] = run.get("renamed", 0) + 1
            for k in ("prompt_tokens", "completion_tokens"):
                run[k] += result.get(k, 0)
            model_errors = model_errors + 1 if result.get("model_error") else 0
            backend_errors = backend_errors + 1 if result.get("backend_error") else 0
            self.say(progress(i, len(todo), f, result))
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

    def _out_of_time(self, run: dict, t0: float, left: int) -> bool:
        if self.time_limit and time.monotonic() - t0 > self.time_limit:
            run["stopped"] = "time limit"
            self.say(f"  time limit reached: {left} functions left for the next run")
            return True
        return False

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

    @staticmethod
    def function_line(g: dict, here: str = "") -> str:
        """Another function as the model reads it: `- name — one-line summary` (or what it is)."""
        if g["address"] == here:
            return f"- {g['orig']} — this function itself (it is recursive)"
        if g["status"] == "done":
            return f"- {g['name']} — {g['summary']}"
        if g["status"] == "known":
            return f"- {g['orig']} — " + ("imported" if g["flags"].get("external") else
                                          "a thunk (a jump to the function of that name)"
                                          if g["flags"].get("thunk") else "named in the binary")
        return f"- {g['orig']} — not named yet"

    def callee_text(self, f: dict) -> str:
        """What the model reads after the code: each callee as `name — one-line summary`."""
        fs = self.book.functions
        callees = f.get("callees") or []
        lines = [self.function_line(fs[c], f["address"]) for c in callees[:CALLEES_SHOWN]]
        if len(callees) > CALLEES_SHOWN:
            lines.append(f"- … and {len(callees) - CALLEES_SHOWN} more")
        text = "It calls:\n" + "\n".join(lines) if lines else "It calls no other function of the program."
        if f.get("outside"):
            text += "\nIt also calls (imports, or not resolved): " + ", ".join(f["outside"][:30])
        return text

    def rename_in(self, code: str, but: str = "") -> str:
        """Default names in the code of functions already named -> their given names (not `but`'s: in the
        second pass a function's own code keeps its decompiler's name, as the heading says it, so the draft's
        name is read once, in the draft, not again in the code's first line)."""
        book = self.book

        def sub(m):
            a = book.resolve(m.group(0))
            g = book.functions.get(a) if a and a != but else None
            return g["name"] if g and g["status"] == "done" and g["name"] else m.group(0)
        return DEFAULT_IN_TEXT.sub(sub, code)

    def code_budget(self, callee_text: str) -> int:
        tokens = self.ctx - self.reserve - FIXED_TOKENS
        return max(1500, int(tokens * CODE_CHARS_PER_TOKEN) - len(callee_text) - 300)

    def _ask(self, messages: list[dict], tools: list[dict], check_call, no_call: str, r: dict):
        """The exchange of a conversation with one tool: the model asked, its call checked, a wrong answer
        or none told back once. -> (record, "", steps, "done") or (None, why, steps, status): "failed", or
        "pending" when the engine does not answer (not the function's fault: the next run takes it)."""
        tool = tools[0]["function"]["name"]
        error = ""
        for step in (1, 2):
            try:
                reply = self.model.chat(messages, tools, max_tokens=self.max_tokens, temperature=self.temperature,
                                        think=self.think, should_stop=self.stop.is_set)
            except ModelError as e:
                r["model_error"] = True
                # An engine gone is not the function's fault: it stays pending for the next run. An
                # answer of the engine against this request (a prompt too long) is.
                gone = "does not answer" in str(e)
                return None, f"engine: {str(e)[:200]}", step, "pending" if gone else "failed"
            r["prompt_tokens"] += reply.prompt_tokens
            r["completion_tokens"] += reply.completion_tokens
            record = None
            for call in reply.tool_calls:
                record, error = check_call(call)
                if record:
                    break
            if record:
                return record, "", step, "done"
            if not reply.tool_calls:
                error = f"no {tool} call" + (f" (cut at {self.max_tokens} tokens)" if reply.finish == "length" else "")
            messages.append(reply.message())
            if reply.tool_calls:
                for call in reply.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": f"error: {error}. Call {tool} again, correctly."})
            else:
                messages.append({"role": "user", "content": no_call})
        return None, error, 2, "failed"

    def _code(self, f: dict, r: dict) -> tuple[str | None, str]:
        """The function's pseudo-code -> (code, "") or (None, why there is none: failed / skipped)."""
        try:
            code = self.adapter.code(f["address"])
        except BackendError as e:
            r["backend_error"] = True
            return None, f"backend: {str(e)[:200]}"
        if not code.strip():
            return None, "the decompiler gave no pseudo-code"
        return code, ""

    @staticmethod
    def heading(f: dict) -> str:
        size = f" ({int(f['size'])} bytes)" if f.get("size") else ""
        return f"Function {f['orig']} at {f['address']}{size}"

    def _one(self, f: dict) -> dict:
        """Analyse one function; the book entry is changed only when it ends (a stop leaves it as it was)."""
        t0 = time.perf_counter()
        r = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

        def end(status: str, reason: str = "", steps: int = 0):
            r["seconds"] = time.perf_counter() - t0
            f.update(status=status, reason=reason, seconds=round(r["seconds"], 2), steps=steps,
                     prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"], updated=book_now())
            return r

        code, why = self._code(f, r)
        if code is None:
            return end("failed" if r.get("backend_error") else "skipped", why)
        f["code_chars"] = len(code)
        callee_text = self.callee_text(f)
        budget = self.code_budget(callee_text)
        body = clip(self.rename_in(code).strip("\n"), budget)
        user = (f"{self.heading(f)}:\n\n```c\n{body}\n```\n\n{callee_text}\n\n"
                "Call record_function for this function.")
        messages = [{"role": "system", "content": WALK_SYSTEM}, {"role": "user", "content": user}]
        record, error, steps, status = self._ask(messages, [RECORD], check, NO_CALL, r)
        if record:
            end("done", "", steps)
            self.book.record(f["address"], **record)
            r["mirror"] = self._write_back(f)
            return r
        return end(status, error, steps)

    def _write_back(self, f: dict, ops=("rename", "comment")) -> str:
        """The name and the summary into the decompiler's database, when allowed. Returns a note for the
        progress line; what happened is in the book."""
        notes, a = [], self.adapter
        values = {"address": f["address"], "name": f["name"],
                  "comment": f["summary"] + (f"\n\n{f['comment']}" if f.get("comment") else "")}
        for op in ops:
            if self.mirror.get(op, "x"):
                continue
            why = a.write(op, **values)
            f["mirror"][op] = "written" if not why else f"failed: {why}"
            if why:
                notes.append(f"{op} failed")
        return ("  (" + ", ".join(notes) + ")") if notes else ""

    # ------------------------------------------------------------ the second pass (--revise)

    def _second_pass(self, order: list[str], run: dict, t0: float, sent: int):
        """From the roots down, every function the first pass named, again: with the program's summary,
        its callers (reviewed already) and its first name as a draft. Only once the first pass is complete."""
        book, fs = self.book, self.book.functions
        pending = sum(1 for f in fs.values() if f["status"] == "pending")
        if pending:
            self.say(f"\nsecond pass: waits for the first ({pending} functions still to name; rerun to go on)")
            return
        run["revise"] = {"depth": self.revise_depth, "skip_high": self.revise_skip_high, **self.revise_with}
        todo = [a for a in order if fs[a]["status"] == "done" and (
            "revision" not in fs[a] or self.retry_failed and fs[a]["revision"].get("status") == "failed")]
        bottom_up = self.revise_with.get("order") == "bottom_up"
        if bottom_up:                                   # the first pass's order: callees first, a cycle as it was
            rank = {a: i for i, a in enumerate(self._first_order or list(reversed(order)))}
            todo.sort(key=lambda a: rank.get(a, len(rank)))
        scope = ""
        if self.revise_depth is not None:
            depth = book.depths(order)
            todo = [a for a in todo if depth.get(a, 1 << 30) <= self.revise_depth]
            scope += f", within {self.revise_depth} calls of a root"
        if self.revise_skip_high:
            todo = [a for a in todo if (fs[a].get("first") or fs[a]).get("confidence") != "high"]
            scope += ", not those named with high confidence"
        if self.max_functions:
            left = max(0, self.max_functions - sent)
            if len(todo) > left:
                scope += f", {left} of {len(todo)} in this run (--max-functions)"
            todo = todo[:left]
        reviewed = sum(1 for f in fs.values() if (f.get("revision") or {}).get("status") == "done")
        self.say(f"\nsecond pass  {len(todo)} functions, " + ("from the leaves up" if bottom_up else "from the roots down")
                 + f"{scope} ({reviewed} reviewed before)")
        if not todo:
            return
        if self.revise_with["program"] and (book.data.get("program") or {}).get("status") != "done":
            if self.stop.is_set() or self._out_of_time(run, t0, len(todo)):
                run["stopped"] = run["stopped"] or "stopped"
                return
            try:
                self._program(run)
            except Stopped:
                run["stopped"] = "stopped"
                return
            book.save()
        self._callers = book.callers()
        for callers in self._callers.values():
            callers.sort(key=sort_key)
        self._functions(todo, run, t0, one=self._revise_one, progress=self._revise_progress)

    def program_facts(self) -> str:
        """What the program's summary is written from: the file, its imports by library, the top of its call
        tree as the first pass named it, and some of its strings when the server lists them."""
        book, fs = self.book, self.book.functions
        info = book.data["binary"]
        size = f" ({info['size']:,} bytes)" if info.get("size") else ""
        c = book.counts()
        parts = [f"File: {info.get('name', '?')}{size}",
                 f"Functions: {len(fs)} ({c['done']} named by the first reading, {c['known']} with names of their "
                 "own: imports, symbols" + (f"; {c['failed'] + c['skipped']} not understood" if c['failed'] + c['skipped']
                                           else "") + ")"]
        libraries: dict[str, list[str]] = {}
        for f in fs.values():
            if f["status"] == "known" and f["flags"].get("external") and f.get("orig"):
                lib, _, name = f["orig"].rpartition("::")
                libraries.setdefault(lib or "(library not given)", []).append(name)
        listed = {n for names in libraries.values() for n in names}
        for f in fs.values():                       # imports a server does not list (pyghidra-mcp): callees by name
            for name in f.get("outside") or []:
                if name not in listed:
                    listed.add(name)
                    libraries.setdefault("(library not given)", []).append(name)
        if libraries:
            lines = []
            for lib, names in sorted(libraries.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:IMPORT_LIBRARIES]:
                names = sorted(set(names))
                more = f", … and {len(names) - IMPORTS_PER_LIBRARY} more" if len(names) > IMPORTS_PER_LIBRARY else ""
                lines.append(f"- {lib} ({len(names)}): " + ", ".join(names[:IMPORTS_PER_LIBRARY]) + more)
            if len(libraries) > IMPORT_LIBRARIES:
                lines.append(f"- … and {len(libraries) - IMPORT_LIBRARIES} more libraries")
            parts.append("Imports, by library:\n" + "\n".join(lines))
        # The top of the tree, chosen level by level (entry and the C runtime's startup take two levels above
        # main in a PE), then drawn as a tree; imports and other named functions with nothing below left out.
        reach: dict[str, int] = {}

        def kids(a):
            found = [k for k in fs[a].get("callees") or [] if k in fs and k != a
                     and (fs[k]["status"] != "known" or fs[k].get("callees"))]
            for k in found:
                if k not in reach:
                    reach[k] = book.reach(k)
            return sorted(found, key=lambda k: (-reach[k], sort_key(k)))
        # Each function is drawn once, under the function the choice reached it through, so the drawing is as
        # deep as the choice (PROGRAM_DEPTH levels) and a function's callees chosen at the next level sit side by
        # side under it. Drawn depth first instead, where.exe's tree went 12 levels down one mislabelled chain,
        # its siblings nested under whichever deep path reached them first (2026-11-13).
        chosen, under, level = [], {}, [(a, None) for a in book.roots()]
        for depth in range(PROGRAM_DEPTH):
            fresh: dict[str, str | None] = {}
            for a, parent in level:
                if a not in under and a not in fresh:
                    fresh[a] = parent
            taken = list(fresh.items())[:PROGRAM_FUNCTIONS - len(chosen)]
            for a, parent in taken:
                chosen.append(a)
                under[a] = parent
            if len(chosen) >= PROGRAM_FUNCTIONS:
                break
            level = [(k, a) for a, _ in taken for k in kids(a)]
        below: dict[str | None, list[str]] = {}
        for a in chosen:
            below.setdefault(under[a], []).append(a)
        tree = []
        stack = [(r, 0) for r in reversed(below.get(None, []))]
        while stack:
            a, depth = stack.pop()
            line = self.function_line(fs[a])
            tree.append("  " * depth + (line if len(line) <= 200 else line[:199] + "…"))
            stack += [(k, depth + 1) for k in reversed(below.get(a, []))]
        if tree:
            parts.append("The call tree from the top, as the first reading named it (the most below first; "
                         "imports left out):\n" + "\n".join(tree))
        texts = []
        if self.adapter is not None and self.adapter.has("strings"):
            try:
                found = self.adapter.strings()
            except Exception as e:                  # optional: the summary goes on without
                book.data.setdefault("program", {})["strings_error"] = str(e)[:200]
                found = []
            seen_text = {n.lower() for n in listed} | {lib.lower() for lib in libraries}
            for s in found:
                s = " ".join(s.split())
                low = s.lower()
                if (low.endswith((".dll", ".drv", ".sys")) and " " not in s or s.startswith(".") and " " not in s
                        or re.fullmatch(r"\?\w+@@\w*", s)):     # libraries, sections, decorated imports
                    continue
                if len(s) >= 4 and low not in seen_text and sum(ch.isalpha() for ch in s) >= 3:
                    seen_text.add(low)
                    texts.append(s if len(s) <= STRING_CHARS else s[:STRING_CHARS - 1] + "…")
            texts = sorted(texts, key=lambda s: " " not in s)[:STRINGS_SHOWN]   # stable: messages first
        if texts:
            parts.append("Some of its strings:\n" + "\n".join(json.dumps(s, ensure_ascii=False) for s in texts))
        self._facts_counts = {"imports": len(listed), "functions": len(tree), "strings": len(texts)}
        return "\n\n".join(parts)

    def _program(self, run: dict):
        """The program's summary: one conversation, one tool (record_program), into the book's `program`."""
        t = time.perf_counter()
        r = {"prompt_tokens": 0, "completion_tokens": 0}
        facts = clip(self.program_facts(), int((self.ctx - self.reserve - FIXED_TOKENS) * 3.0))
        messages = [{"role": "system", "content": PROGRAM_SYSTEM},
                    {"role": "user", "content": f"{facts}\n\nCall record_program for this program."}]
        record, error, steps, status = self._ask(messages, [RECORD_PROGRAM], check_program, NO_CALL_PROGRAM, r)
        program = self.book.data.setdefault("program", {})
        program.update(status="done" if record else "failed", reason=error, summary=(record or {}).get("summary", ""),
                       seconds=round(time.perf_counter() - t, 2), steps=steps, prompt_tokens=r["prompt_tokens"],
                       completion_tokens=r["completion_tokens"], inputs=self._facts_counts, facts=facts,
                       model=self.model_label, updated=book_now())
        run["program"] = program["status"]
        for k in ("prompt_tokens", "completion_tokens"):
            run[k] += r[k]
        self.say(f"  program  {program['summary']}" if record else f"  program  not summarized: {error}"
                 + " (the second pass goes on without it)")

    def caller_text(self, f: dict) -> str:
        fs = self.book.functions
        callers = (self._callers or {}).get(f["address"]) or []
        if not callers:
            return "Nothing in the program calls it directly (an entry point, or called through a pointer or a table)."
        lines = []
        for c in callers[:CALLERS_SHOWN]:
            lines.append(self.function_line(fs[c]))
            if self.revise_with.get("call_lines"):
                lines += [f"    `{line}`" for line in self.call_lines(c, f)]
        if len(callers) > CALLERS_SHOWN:
            lines.append(f"- … and {len(callers) - CALLERS_SHOWN} more")
        head = ("It is called by (as the first reading named them: not read again yet):"
                if self.revise_with.get("order") == "bottom_up" else "It is called by:")
        return head + "\n" + "\n".join(lines)

    def call_lines(self, caller: str, f: dict) -> list[str]:
        """--revise-call-lines: the lines of a caller's code that call `f` (not a mention, not the caller's own
        signature), at most CALL_LINES. Other functions under their current names; `f` under its decompiler's
        name when the draft is hidden, else under its current name."""
        g = self.book.functions[caller]
        code = self._caller_code.get(caller)
        if code is None:
            try:
                code = self.adapter.code(caller) if has_code(g) and self.adapter is not None else ""
            except BackendError:
                code = ""
            self._caller_code[caller] = code
        hidden = self.revise_with.get("draft", True) is False
        names = {n for n in (f["orig"], f["name"], (f.get("first") or {}).get("name")) if n}
        rx = re.compile(r"(?<![\w.])(?:" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![\w.])")
        shown = f["orig"] if hidden else f["name"]
        out = []
        for line in code.replace("\r\n", "\n").splitlines():
            m = rx.search(line)
            if not m or not re.match(r"\s*\(", line[m.end():]):
                continue                                    # a mention, not a call
            if line[:1] not in (" ", "\t") and line.rstrip().endswith(")"):
                continue                                    # the caller's own signature, at column 0
            line = rx.sub(shown, self.rename_in(line.strip(), but=f["address"]))
            out.append(line if len(line) <= CALL_LINE_CHARS else line[:CALL_LINE_CHARS - 1] + "…")
            if len(out) >= CALL_LINES:
                break
        return out

    def revise_code(self, f: dict, code: str) -> str:
        """The code with the current names: the decompiler's default names as in the first pass, and the
        first-pass names of callees renamed since, where the decompiler still says them (a rename it was
        not given or that failed), unless that old name is someone else's now."""
        code = self.rename_in(code, but=f["address"])
        fs, taken = self.book.functions, None
        for c in f.get("callees") or []:
            old = self.book.renamed(fs[c]) if c in fs else ""
            if not old or old == fs[c]["name"]:
                continue
            taken = self.book.taken() if taken is None else taken
            if old not in taken:
                code = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w.])", fs[c]["name"], code)
        if self.revise_with.get("draft", True) is False and f["name"] and f["orig"] and f["name"] != f["orig"]:
            # The draft hidden: a decompiler written back to (pyghidra-mcp) says the function's first name in
            # its own code; it goes back to the decompiler's, as the heading says it.
            code = re.sub(rf"(?<![\w.]){re.escape(f['name'])}(?![\w.])", f["orig"], code)
        return code

    @staticmethod
    def draft_text(f: dict) -> str:
        return ("The first reading's draft, made seeing only the functions it calls:\n"
                f"- name: {f['name']}\n- summary: {f['summary']}\n- confidence: {f.get('confidence') or 'low'}")

    def revise_prompt(self, f: dict, code: str) -> str:
        program = (self.book.data.get("program") or {}).get("summary", "") if self.revise_with["program"] else ""
        head = [f"What the program is (from a first look at all of it): {program}"] if program else []
        head.append(f"{self.heading(f)}.")
        if self.revise_with["callers"]:
            head.append(self.caller_text(f))
        callee_text, draft = self.callee_text(f), self.draft_text(f)
        # The same budget with the draft hidden (counted, not shown): the code is clipped the same way in both.
        budget = self.code_budget("\n\n".join(head) + callee_text + draft) - 600     # its system prompt is longer
        body = clip(self.revise_code(f, code).strip("\n"), max(1500, budget))
        if self.revise_with.get("draft", True) is False:
            return ("\n\n".join(head) + f"\n\nIts pseudo-code:\n\n```c\n{body}\n```\n\n{callee_text}\n\n"
                    "Call record_function for this function: name it from its code, from what calls it and from "
                    "what the program is.")
        return ("\n\n".join(head) + f"\n\nIts pseudo-code:\n\n```c\n{body}\n```\n\n{callee_text}\n\n{draft}\n\n"
                "Call record_function for this function: keep the draft's name if it is right, change it only if "
                "it is wrong or misleading.")

    def _revise_one(self, f: dict) -> dict:
        """Review one function; its entry is changed only when it ends (a stop leaves it unreviewed)."""
        t0 = time.perf_counter()
        r = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

        def cost(steps: int) -> dict:
            r["seconds"] = time.perf_counter() - t0
            return {"seconds": round(r["seconds"], 2), "steps": steps, "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"], "with": dict(self.revise_with)}

        def end(status: str, reason: str, steps: int = 0):
            if status == "pending":                     # the engine gone: not reviewed, the next run takes it
                r["status"] = "revise_pending"
                return r
            f["revision"] = {"status": status, "reason": reason, **cost(steps), "updated": book_now()}
            r["status"] = f"revise_{status}"
            return r

        code, why = self._code(f, r)
        if code is None:
            return end("failed" if r.get("backend_error") else "skipped", why)
        system = revise_system(self.revise_with.get("draft", True) is not False,
                               self.revise_with.get("order") == "bottom_up", bool(self.revise_with.get("call_lines")))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": self.revise_prompt(f, code)}]
        before = (f["name"], f["summary"], f.get("comment") or "")
        record, error, steps, status = self._ask(messages, [RECORD], check, NO_CALL, r)
        if not record:
            return end(status, error, steps)
        renamed = self.book.revise(f["address"], **record, **cost(steps))
        r["status"], r["renamed"], r["was"] = "revised", renamed, before[0]
        ops = (["rename"] if renamed else []) + (["comment"] if (f["summary"], f["comment"]) != before[1:] else [])
        r["mirror"] = self._write_back(f, ops)
        f["revision"]["mirror"] = {op: f["mirror"][op] for op in ops if not self.mirror.get(op, "x")}
        f["revision"]["summary_changed"] = "comment" in ops
        return r

    def _revise_progress(self, i: int, n: int, f: dict, r: dict) -> str:
        head = f"[{i}/{n}] {f['address']} "
        tail = f"  {r.get('seconds', 0.0):.1f} s  {r.get('prompt_tokens', 0)}+{r.get('completion_tokens', 0)} tok"
        if r.get("status") == "revised":
            what = f"{r['was']} -> {f['name']}" if r["renamed"] else f"{f['name']} kept"
            return f"{head}{what}  [{f['confidence']}]{tail}{r.get('mirror', '')}"
        reason = (f.get("revision") or {}).get("reason", "") if r.get("status") != "revise_pending" else "engine gone"
        return f"{head}{f['name']} {r.get('status', '').replace('revise_', 'review ')}: {reason}{tail}"

    def _summary(self, run: dict):
        c = self.book.counts()
        analysed = sum(run.get(k, 0) for k in ("done", "failed", "revised", "revise_failed"))   # both passes
        mean = sum(f["seconds"] for f in self.book.functions.values()
                   if f["status"] in ("done", "failed")) / max(1, c["done"] + c["failed"])
        self.say(f"\nthis run: {run.get('done', 0)} named, {run.get('failed', 0)} failed, {run.get('skipped', 0)} "
                 f"skipped in {run['seconds']:.0f} s, {run['prompt_tokens']}+{run['completion_tokens']} tokens"
                 + (f", {run['seconds'] / analysed:.1f} s per function" if analysed else "")
                 + (f"; stopped: {run['stopped']}" if run.get("stopped") else ""))
        if "revise" in run:
            self.say(f"second pass in this run: {run.get('revised', 0)} reviewed ({run.get('renamed', 0)} renamed), "
                     f"{run.get('revise_failed', 0)} failed, {run.get('revise_skipped', 0)} skipped"
                     + (f"; program summary {run['program']}" if run.get("program") else ""))
        revised = [f for f in self.book.functions.values() if (f.get("revision") or {}).get("status") == "done"]
        self.say(f"the book: {len(self.book.functions)} functions, {c['done']} done, {c['known']} known, "
                 f"{c['skipped']} skipped, {c['failed']} failed, {c['pending']} pending; mean {mean:.1f} s per "
                 f"function"
                 + (f"; second pass: {len(revised)} reviewed, {sum(1 for f in revised if self.book.renamed(f))} "
                    "renamed" if revised else "")
                 + f"\n  {self.book.dir / 'book.md'}")


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
                    retry_failed=args.retry_failed, model_label=label_,
                    # any --revise-* option implies --revise
                    revise=bool(getattr(args, "revise", False) or getattr(args, "revise_depth", None) is not None
                                or any(getattr(args, k, False) for k in ("revise_skip_high", "revise_no_program",
                                                                         "revise_no_callers", "revise_no_draft",
                                                                         "revise_bottom_up", "revise_call_lines"))),
                    revise_depth=getattr(args, "revise_depth", None),
                    revise_skip_high=bool(getattr(args, "revise_skip_high", False)),
                    revise_program=not getattr(args, "revise_no_program", False),
                    revise_callers=not getattr(args, "revise_no_callers", False),
                    revise_draft=not getattr(args, "revise_no_draft", False),
                    revise_bottom_up=bool(getattr(args, "revise_bottom_up", False)),
                    revise_call_lines=bool(getattr(args, "revise_call_lines", False)))
        result = walk.run(binary)
        return 0 if not result.get("stopped") or result["stopped"] in ("time limit", "interrupted", "stopped") else 1
    finally:
        stop.set()
        if walk is not None and walk.adapter is not None:
            walk.adapter.close()
        server.close()
        if engine is not None:
            engine.close()
