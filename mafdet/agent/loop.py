"""The agent loop: the model writes, asks for tools, reads their results, and goes on until it
answers without asking for any.

What a local model gets wrong is handled here and told back to it as a tool result, so it can
correct itself: arguments that are not JSON, a tool that does not exist, a call the user declined,
a tool that raised. Tool outputs are cut to size and old ones left out (`context.py`); the
conversation is written to disk as it goes (`store.py`).
"""
from __future__ import annotations

import json
import threading
import time

from .context import ROOM, Context, ContextFull, clip, head, stub
from .memory import KEEP_GROUPS, Memory, is_summary, request_in, summarize, summary_message, tail_from
from .model import ModelError, Reply, Stopped
from .mcp import Toolbox
from .permissions import Permissions
from .store import Session

SAVE_FROM = 400          # outputs longer than this are also kept whole on disk
USER_CHARS = 3000        # a user's message longer than this is cut, head and tail, by a compaction (whole on disk)
HEAD_MIN = 300           # characters: a shorter head of the last output is not worth keeping after a compaction


class Agent:
    def __init__(self, model, toolbox: Toolbox, permissions: Permissions, session: Session, *, system: str,
                 ctx: int = 16384, max_tokens: int = 4096, max_steps: int = 40, tool_chars: int = 4000,
                 temperature: float = 0.6, think: bool = True, emit=None, history: list[dict] | None = None,
                 stop: threading.Event | None = None, memory: Memory | None = None):
        self.model = model
        self.memory = memory or Memory()
        self.toolbox = toolbox
        self.permissions = permissions
        self.session = session
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.tool_chars = tool_chars
        self.temperature = temperature
        self.think = think
        self.emit = emit or (lambda kind, **data: None)
        self.context = Context(ctx, reserve=min(max_tokens, ctx // 3))
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.paths: dict[str, str] = {}                 # tool_call_id -> the file with its whole output
        self.tokens = 0                                 # the prompt of the last request, as the engine counted it
        self.stop = stop or threading.Event()           # set it to end the turn (Esc); the tools see it too
        if history:
            self.messages += history
            self.paths = session.output_paths()
            self.memory.clear(session.dir / "notes.md")
            self.memory.notes = session.notes()
            self.memory.todo = session.todo()       # the list as it was last set, not one from a summary
            self.memory.changed()
        else:
            self.memory.clear(session.dir / "notes.md")
            session.log(self.messages[0])

    def _add(self, message: dict):
        self.messages.append(message)
        self.session.log(message)

    def run(self, text: str) -> str:
        """One turn of the user: returns the model's final answer ("" when it was cut short)."""
        self.stop.clear()
        self._add({"role": "user", "content": text})
        self.turn_start = len(self.messages)
        try:
            return self._steps()
        except Stopped:                     # what the model was writing is dropped; the history stays valid
            self._close_calls("interrupted by the user")
            self.emit("stopped")
            return ""
        except KeyboardInterrupt:
            self._close_calls("interrupted by the user")
            raise

    def _close_calls(self, why: str):
        """Every tool call in the history must have a result, or the next request is refused."""
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for m in list(self.messages):
            for call in m.get("tool_calls") or []:
                if call["id"] not in answered:
                    self._add({"role": "tool", "tool_call_id": call["id"], "content": why})

    def compact(self) -> bool:
        """Replace the conversation with the model's summary of it, the notes, the list, the user's last
        message and the last turns, their tool outputs reduced to where they are on disk.

        What it leaves is small by construction, and it aims at ROOM of the budget, as leaving outputs out
        does: first the last two turns, with as much of the most recent output as the aim leaves room for;
        if that is over it, one turn, all its outputs reduced. Nothing is written to the session unless the
        result is smaller than what it replaces and fits; otherwise the error says what does not fit.
        (The first real compactions summarized outputs already left out and kept the big ones whole in the
        last two turns: 4564 -> 4696 tokens in a 6k context, and the turn failed.)"""
        tools = self.toolbox.specs()
        before = self.context.estimate(self.messages, tools)
        aim = int(self.context.limit * ROOM)
        first = tail_from(self.messages, KEEP_GROUPS)
        last = max((i for i, m in enumerate(self.messages) if m.get("role") == "user"), default=0)
        users = {i: self._cut_user(m) for i, m in enumerate(self.messages)
                 if m.get("role") == "user" and (i == last or i >= first)}
        summaries, best = {}, None
        for groups, keep_head in ((KEEP_GROUPS, True), (1, False)):
            cut = tail_from(self.messages, groups)
            if cut not in summaries:
                try:
                    summaries[cut] = summarize(self.messages[1:cut], self.model, self.context.limit,
                                               self.context.ratio)
                except ModelError as e:
                    self.emit("error", text=f"no summary: {e}")
                    return False
            if summaries[cut] is None:
                continue
            new = self._rebuilt(summaries[cut], cut, last, users, tools, aim if keep_head else 0)
            size = self.context.estimate(new, tools)
            if best is None or size < best[0]:
                best = (size, new)
            if size <= aim:
                break
        if best is None:
            self.emit("error", text="no summary: the model wrote nothing")
            return False
        size, new = best
        if size >= before or size > self.context.limit:
            self.emit("error", text=self._no_room(size, before, tools))
            return False
        self.messages = new
        self.session.reset(new[1:])
        self.emit("compacted", before=before, after=size)
        return True

    def _rebuilt(self, summary: str, cut: int, last: int, users: dict, tools: list[dict], aim: int) -> list[dict]:
        """[system, summary + notes + list + the user's last message, the turns from `cut` on, reduced]. With
        an `aim`, the most recent long output keeps the head that fits under it."""
        msgs = self.messages
        tail, recent = [], None
        for i, m in enumerate(msgs[cut:], cut):
            if m.get("role") == "tool" and len(str(m.get("content") or "")) > SAVE_FROM:
                m = dict(m, content=stub(self._where(m)))
                recent = (len(tail) + 2, i)
            elif i in users:
                m = dict(m, content=users[i])
            tail.append(m)
        last_user = users[last] if 0 < last < cut else ""
        new = [msgs[0], {"role": "user", "content": summary_message(summary, last_user, self.memory)}] + tail
        if aim and recent:
            k, i = recent
            reduced = new[k]
            chars = min(int((aim - self.context.estimate(new, tools)) / max(self.context.ratio, 0.15)),
                        self.tool_chars // 2)
            while chars >= HEAD_MIN:                # the head's own line and JSON's escapes take a little more
                new[k] = dict(reduced, content=head(str(msgs[i]["content"]), chars, self._where(msgs[i])))
                if self.context.estimate(new, tools) <= aim:
                    break
                new[k], chars = reduced, int(chars * 0.8)
        return new

    def _where(self, message: dict) -> str:
        """The file with the whole of a tool's output: saved when it was made, or now (a session resumed
        from before the files were indexed)."""
        call_id = message.get("tool_call_id", "")
        if call_id not in self.paths:
            name = next((c.get("function", {}).get("name", "") for m in self.messages
                         for c in m.get("tool_calls") or [] if c.get("id") == call_id), "") or "output"
            self.paths[call_id] = self.session.save_output(name, str(message.get("content") or ""), call_id)
        return self.paths[call_id]

    def _cut_user(self, message: dict) -> str:
        """A user's message as a compaction keeps it: cut when long (whole on disk). A summary message of an
        earlier compaction gives the message it quoted, cut already, or nothing."""
        text = str(message.get("content") or "")
        if is_summary(text):
            quoted = request_in(text)
            return "" if quoted == text else quoted
        if len(text) <= USER_CHARS:
            return text
        return clip(text, USER_CHARS, self.session.save_output("user-message", text))

    def _no_room(self, size: int, before: int, tools: list[dict]) -> str:
        """Why a compaction did not help, in tokens."""
        limit = self.context.limit
        fixed = self.context.estimate(self.messages[:1], tools)
        kept = self.context.estimate([{"role": "user", "content": self.memory.text()}], None) if self.memory.text() else 0
        if fixed > limit:
            return (f"the system prompt and the tools' schemas alone take about {fixed} tokens, more than the {limit} "
                    "the context leaves for the prompt: give the model a larger context (--ctx), or fewer tools")
        if fixed + kept > limit:
            return (f"the system prompt and the tools' schemas (about {fixed} tokens) with the notes and the to-do "
                    f"list (about {kept}) take more than the {limit} the context leaves for the prompt: give the "
                    "model a larger context (--ctx)")
        if size > limit:
            return (f"even summarized, the conversation would take about {size} tokens, more than the {limit} the "
                    f"context leaves for the prompt (the system prompt and the tools' schemas about {fixed}, the "
                    f"notes and the list about {kept}): start a new one (/new), or give the model a larger context (--ctx)")
        return f"nothing to gain: summarized, the conversation would take about {size} tokens, and it takes {before} now"

    def _fit(self, tools: list[dict]) -> bool:
        """Make the prompt fit: old outputs left out first, then the conversation summarized."""
        try:
            left_out = self.context.fit(self.messages, tools, self.paths)
        except ContextFull:
            if not self.compact():              # it said why
                return False
            try:
                left_out = self.context.fit(self.messages, tools, self.paths)
            except ContextFull as e2:
                self.emit("error", text=str(e2))
                return False
        if left_out:
            self.emit("left_out", n=left_out)
        return True

    def _steps(self) -> str:
        for step in range(1, self.max_steps + 1):
            tools = self.toolbox.specs()
            if not self._fit(tools):
                return ""
            t0 = time.perf_counter()
            try:
                reply: Reply = self.model.chat(self.messages, tools, max_tokens=self.max_tokens,
                                               temperature=self.temperature, think=self.think,
                                               on_delta=lambda kind, piece: self.emit(kind, text=piece),
                                               should_stop=self.stop.is_set)
            except ModelError as e:
                self.emit("error", text=str(e))
                return ""
            self.context.calibrate(self.messages, tools, reply.prompt_tokens)
            self.tokens = reply.prompt_tokens + reply.completion_tokens
            self.emit("step", n=step, seconds=time.perf_counter() - t0, prompt_tokens=reply.prompt_tokens,
                      completion_tokens=reply.completion_tokens, limit=self.context.limit)
            self._add(reply.message())
            if not reply.tool_calls:
                if reply.finish == "length":
                    self.emit("error", text=f"cut at {self.max_tokens} tokens (--max-tokens)"
                                            + ("" if reply.content else ", before any answer"))
                return reply.content
            for call in reply.tool_calls:
                if self.stop.is_set():
                    raise Stopped
                self._call(call)
        self.emit("error", text=f"stopped after {self.max_steps} steps (--max-steps): say `continue` to go on")
        return ""

    def _call(self, call: dict):
        name = call["name"]
        try:
            args = json.loads(call["arguments"] or "{}")
            if not isinstance(args, dict):
                raise ValueError("not an object")
        except ValueError as e:
            self.emit("tool", name=name, args=call["arguments"])
            return self._result(call, f"error: the arguments are not a JSON object ({e}): call {name} again", 0.0)
        self.emit("tool", name=name, args=args)
        tool = self.toolbox.get(name)
        if tool is None and name in self.toolbox.hidden:
            return self._result(call, f'error: {name} is off: turn it on with find_tools(enable=["{name}"]), '
                                      "then call it", 0.0)
        if tool is None:
            return self._result(call, f"error: no tool named {name}. The tools are: "
                                      + ", ".join(self.toolbox.active), 0.0)
        missing = [k for k in tool.parameters.get("required") or [] if k not in args]
        if missing:
            return self._result(call, f"error: {name} needs {', '.join(missing)}", 0.0)
        turn = self.messages[getattr(self, "turn_start", len(self.messages)):]
        if not self.permissions.allowed(tool, args, turn):
            self.emit("declined", name=name)
            return self._result(call, "The user declined this call. Do not repeat it: go on another way, "
                                      "or say what you need and why.", 0.0)
        t0 = time.perf_counter()
        try:
            out = str(tool.run(args))
        except Exception as e:                      # a tool's failure is information for the model
            out = f"error: {type(e).__name__}: {e}"
        self._result(call, out, time.perf_counter() - t0)

    def _result(self, call: dict, out: str, seconds: float):
        where = ""
        if len(out) > SAVE_FROM:
            where = self.paths[call["id"]] = self.session.save_output(call["name"], out, call["id"])
        shown = clip(out, self.tool_chars, where)
        self.emit("result", name=call["name"], text=shown, chars=len(out), seconds=seconds)
        self._add({"role": "tool", "tool_call_id": call["id"], "content": shown})
