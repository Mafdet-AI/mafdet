"""Thinking ahead in the agent: the model reasons about the message while the user writes it.

MAFDET's own idea, as in the chat and the proxy (`proxy/think.py`, whose frame and Thinker this uses
unchanged): while the user types, the conversation as it is (the system prompt with the tools, the
history) plus the draft is rendered by llama-server's own template, and the model writes notes after
it, in short chunks, on llama-server's raw `/completion` route; when the user sends, the first step of
the turn starts from those notes ("the user has now sent the complete message") instead of from an
empty reasoning, and llama-server reuses the cached prefix. The answer is read back as the agent's
other steps are (reasoning, text, Qwen3's <tool_call> blocks as tool calls); the later steps of the
turn go through `/v1/chat/completions` as before.

Only where it can work: a llama-server (the raw route), a chat template with a reasoning block, thinking
on, the full-screen interface (which sees the typing). The agent must be idle: while a turn runs the
slot is the turn's. Not exact, as in the chat: the reasoning started on a message not finished yet.
"""
from __future__ import annotations

import json
import threading
import time

from .model import ModelError, Reply, Stopped

DEBOUNCE = 0.15                 # seconds of quiet typing before the draft is rendered again...
MAX_WAIT = 0.6                  # ...and at least this often while the typing goes on (the proxy's user script:
                                # 250/600 ms). With the quiet alone a steady typist was never rendered until
                                # the end, and the notes started after Enter (B1, 2026-11-14)

# The agent's own frame. The chat's SEED gathers facts about each key term, which suits a question
# ("Quanto pesa la Luna..."); on an agent's request it made the 4B list decades of history from "read
# notes.txt and" (the first real run, 2026-11-12). Here the notes prepare the work instead: what the
# words point to among the files and tools, and the first step. The bridge and the bias stay the chat's.
SEED = ("<think>\nThe user is still writing a request for me. I will not guess the rest of it, nor remark that it "
        "is unfinished. While I wait, I prepare the work: what the words written so far point to (files, "
        "programs, addresses, tools I have), what I would need to find out, and the first step I would take with "
        "my tools. Short lines.\n\n- ")
SENT = "\n\nThe user has now sent the complete request. Let me reread it and act on it, using the notes above.\n\n"


class _Cache:
    """What the Thinker asks of the proxy's shared cache: whether the slot is taken, a lock held while it
    writes a chunk, an event that cuts a chunk short."""

    def __init__(self):
        self.warm_lock = threading.Lock()
        self.abort = threading.Event()
        self.busy = False

    @property
    def generating(self) -> bool:
        return self.busy

    def set(self, ids, **kw):
        pass

    def take(self, wait: float = 10.0):
        """The slot for a request of the agent: the chunk in flight cut short, none started after."""
        self.busy = True
        self.abort.set()
        if self.warm_lock.acquire(timeout=wait):
            self.warm_lock.release()
        self.abort.clear()

    def give_back(self):
        self.busy = False


class Ahead:
    """The draft's reasoning for one agent at a time (`agent` is set by the interface, again after /new)."""

    def __init__(self, engine, ctx: int = 16384):
        from ..proxy import think
        self.think = think
        self.engine = engine
        self.ctx = ctx
        self.frame = think.Frame(engine)
        self.frame.seed = engine.tokenize(SEED) or []
        self.frame.seed_en = []                     # the agent's frame has no example line to translate
        self.frame.sent = engine.tokenize(SENT) or []
        if not self.frame.usable:
            raise ValueError("the model's tokenizer does not give the frame's tokens")
        self.cache = _Cache()
        self.lock = threading.Lock()                # the proxy's DraftSession lock: latest and latest_text
        self.latest: list[int] = []
        self.latest_text = ""
        self.history = ""                           # the conversation the notes were written after
        self.agent = None
        self.paused = False                         # a turn runs
        self.on = True
        self._pending: str | None = None
        self._wake = threading.Event()
        self._stopped = False
        self.thinker = think.Thinker(self, self.frame, enabled=lambda: self.on and not self.paused)
        self.used = 0                               # turns that started from notes
        threading.Thread(target=self._render_loop, daemon=True).start()

    @classmethod
    def for_engine(cls, url: str, ctx: int, slot: int = 0) -> "Ahead | None":
        """None when this engine cannot do it (not llama-server, no reasoning block)."""
        from ..proxy.adapters.llama import LlamaEngine
        engine = LlamaEngine(url, slot=slot)
        try:
            if not engine.thinks():
                return None
            return cls(engine, ctx)
        except (OSError, ValueError):
            return None

    def close(self):
        self._stopped = True
        self.thinker.stopped = True
        self._wake.set()
        self.thinker.poke()

    # ------------------------------------------------------------ while the user types

    def typed(self, text: str):
        """The message box changed."""
        self._pending = text
        self._wake.set()

    @staticmethod
    def _snapshot(messages: list[dict]) -> str:
        return json.dumps(messages, sort_keys=True, ensure_ascii=False)

    def _render_loop(self):
        last = time.monotonic()                                 # the last render
        while not self._stopped:
            self._wake.wait()
            self._wake.clear()
            time.sleep(DEBOUNCE)
            if self._stopped:
                continue
            if self._wake.is_set() and time.monotonic() - last < MAX_WAIT:
                continue                                        # still typing: the next round renders
            last = time.monotonic()
            text, agent = self._pending, self.agent
            if text is None or agent is None or self.paused:
                continue
            if len(text.split()) < self.think.THINK_MIN_WORDS:
                with self.lock:
                    self.latest_text = text
                continue
            try:
                history = list(agent.messages)
                ids = self.engine.render(history + [{"role": "user", "content": text}], {"enable_thinking": True},
                                         agent.toolbox.specs())
            except Exception:                                   # best effort: never in the user's way
                continue
            if not ids or len(ids) + self.think.THINK_CAP + 512 > self.ctx:
                continue
            snap = self._snapshot(history)
            with self.lock:
                if snap != self.history:                        # another conversation: old notes mean nothing
                    self.thinker.reset()
                    self.history = snap
                self.latest, self.latest_text = ids, text
            self.thinker.poke()

    @property
    def reasoned(self) -> int:
        return self.thinker.reasoned

    # ------------------------------------------------------------ on Enter

    def answer(self, messages: list[dict], tools: list[dict], *, max_tokens: int, temperature: float,
               on_delta=None, should_stop=None) -> Reply | None:
        """The first step of a turn from the notes, or None (then the step goes as usual): no notes, or
        written after another conversation."""
        if not messages or messages[-1].get("role") != "user":
            return None
        self.cache.take()
        try:
            with self.lock:
                same = self._snapshot(messages[:-1]) == self.history
            notes = self.thinker.take(str(messages[-1].get("content") or "")) if same else None
            if not notes:
                return None
            prompt = self.engine.render(messages, {"enable_thinking": True}, tools)
            if not prompt:
                return None
            room = self.ctx - len(prompt) - len(notes)
            if room < 256:
                return None
            body = {"max_tokens": min(max_tokens, room), "temperature": temperature}
            reply = Reply()
            for chunk in self.think.answer(self.engine, prompt, notes, self.frame, body):
                if should_stop is not None and should_stop():
                    raise Stopped
                choice = chunk["choices"][0]
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    reply.reasoning += delta["reasoning_content"]
                    if on_delta:
                        on_delta("think", delta["reasoning_content"])
                if delta.get("content"):
                    reply.content += delta["content"]
                    if on_delta:
                        on_delta("text", delta["content"])
                for call in delta.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    reply.tool_calls.append({"id": call.get("id") or f"call_{len(reply.tool_calls)}",
                                             "name": fn.get("name", ""), "arguments": fn.get("arguments") or "{}"})
                if choice.get("finish_reason"):
                    reply.finish = choice["finish_reason"]
                    usage = chunk.get("usage") or {}
                    reply.prompt_tokens = usage.get("prompt_tokens") or 0
                    reply.completion_tokens = usage.get("completion_tokens") or 0
            if reply.finish == "" and not reply.content and not reply.tool_calls:
                raise ModelError("the engine stopped answering")
            reply.content = reply.content.strip()
            self.used += 1
            return reply
        except (OSError, ValueError) as e:
            raise ModelError(f"the engine failed answering from the notes: {e}") from None
        finally:
            self.thinker.reset()
            with self.lock:
                self.latest, self.latest_text = [], ""
            self.cache.give_back()


class AheadModel:
    """The agent's model, with the first step of a turn answered from the notes when there are some."""

    def __init__(self, model, ahead: Ahead):
        self.model = model
        self.ahead = ahead

    def __getattr__(self, name):
        return getattr(self.model, name)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 4096,
             temperature: float = 0.6, think: bool = True, on_delta=None, should_stop=None) -> Reply:
        if think and self.ahead.on:
            reply = self.ahead.answer(messages, tools or [], max_tokens=max_tokens, temperature=temperature,
                                      on_delta=on_delta, should_stop=should_stop)
            if reply is not None:
                return reply
        self.ahead.cache.take()
        try:
            return self.model.chat(messages, tools, max_tokens=max_tokens, temperature=temperature, think=think,
                                   on_delta=on_delta, should_stop=should_stop)
        finally:
            self.ahead.cache.give_back()
