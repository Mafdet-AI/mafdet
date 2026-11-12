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

from .context import Context, ContextFull, clip
from .memory import Memory, compact
from .model import ModelError, Reply, Stopped
from .mcp import Toolbox
from .permissions import Permissions
from .store import Session

SAVE_FROM = 400          # outputs longer than this are also kept whole on disk


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
            self.memory.clear(session.dir / "notes.md")
            self.memory.notes = session.notes()
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
        """Replace the conversation with the model's summary of it, the notes, the list and the last turns."""
        try:
            new = compact(self.messages, self.model, self.memory, self.context.limit, self.context.ratio)
        except ModelError as e:
            self.emit("error", text=f"no summary: {e}")
            return False
        if new is None:
            return False
        before = self.context.estimate(self.messages, self.toolbox.specs())
        self.messages = new
        self.session.reset(new[1:])
        self.emit("compacted", before=before, after=self.context.estimate(new, self.toolbox.specs()))
        return True

    def _fit(self, tools: list[dict]) -> bool:
        """Make the prompt fit: old outputs left out first, then the conversation summarized."""
        try:
            left_out = self.context.fit(self.messages, tools, self.paths)
        except ContextFull as e:
            if not self.compact():
                self.emit("error", text=str(e))
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
        if tool is None:
            return self._result(call, f"error: no tool named {name}. The tools are: "
                                      + ", ".join(self.toolbox.active), 0.0)
        missing = [k for k in tool.parameters.get("required") or [] if k not in args]
        if missing:
            return self._result(call, f"error: {name} needs {', '.join(missing)}", 0.0)
        if not self.permissions.allowed(tool, args):
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
            where = self.paths[call["id"]] = self.session.save_output(call["name"], out)
        shown = clip(out, self.tool_chars, where)
        self.emit("result", name=call["name"], text=shown, chars=len(out), seconds=seconds)
        self._add({"role": "tool", "tool_call_id": call["id"], "content": shown})
