"""A fake engine: enough of the adapter contract to test the probe and the heartbeat of
the proxy without a GPU, a model or a server.

Its answers are deliberately configurable so each test can be one *behaviour*:
no `n_predict:0` support, a cache that only matches whole prompts, no reuse at all,
telemetry that lies, and so on.
"""
from __future__ import annotations

import json
import time

from mafdet.proxy.engine import WarmResult
from mafdet.text import common_prefix_len


class FakeEngine:
    name = "fake"
    dialect = "llama"

    def __init__(self, *, n_predict0: bool = True, reuse: bool = True, branch: bool = True,
                 channel: str = "timings", metrics: str | None = None,
                 report_cached: int | None = None):
        self.n_predict0 = n_predict0          # False: only the one-token fallback works
        self.reuse = reuse                    # False: the cache never matches anything
        self.branch = branch                  # False: only an identical prompt is reusable
        self.channel = channel                # "timings" | "usage" | "none"
        self.metrics = metrics                # text of /metrics, or None
        self.report_cached = report_cached    # lie about this number if set
        self._vocab: dict[str, int] = {}
        self._held: list[int] = []
        self.warms = 0
        self.chats = 0

    # ---- Engine --------------------------------------------------------------
    def tokenize(self, text: str) -> list[int] | None:
        return [self._vocab.setdefault(word, len(self._vocab) + 1) for word in text.split()]

    def render(self, messages: list[dict], template_kwargs: dict | None = None,
               tools: list | None = None) -> list[int] | None:
        """Stand-in for a chat template: role names and contents, deterministically, and
        an empty think block when asked not to think (as Qwen3's template does)."""
        self.last_template_kwargs = template_kwargs
        self.last_messages = list(messages or [])
        self.last_tools = tools
        text = " ".join(f"{m.get('role')}:{m.get('content')}" for m in messages or [])
        if tools:
            text = "tools:" + ",".join(t.get("function", {}).get("name", "") for t in tools) + " " + text
        if (template_kwargs or {}).get("enable_thinking") is False:
            text += " <think></think>"
        return self.tokenize(text)

    def _cached_for(self, ids: list[int]) -> int:
        if not self.reuse:
            return 0
        if not self.branch:
            return max(0, len(ids) - 1) if ids == self._held else 0
        return max(0, common_prefix_len(self._held, ids) - 1)   # last token is recomputed

    def warm(self, ids: list[int], *, spelling: str = "auto") -> WarmResult:
        self.warms += 1
        if not ids:
            return WarmResult(channel="none", detail={"ok": False, "why": "empty prompt"})
        if spelling == "n_predict0" and not self.n_predict0:
            return WarmResult(channel="none", detail={"ok": False, "status": 400})
        used = "max_tokens1" if (spelling == "max_tokens1" or not self.n_predict0) else "n_predict0"
        cached = self._cached_for(ids)
        self._held = list(ids)
        if self.report_cached is not None:
            cached = self.report_cached
        if self.channel == "timings":
            return WarmResult(cached_tokens=cached, computed_tokens=len(ids) - cached, ms=4.0,
                              channel="timings", detail={"ok": True, "spelling": used})
        if self.channel == "usage":
            return WarmResult(cached_tokens=cached, computed_tokens=len(ids) - cached, ms=4.0,
                              channel="usage", detail={"ok": True, "spelling": used})
        return WarmResult(channel="none", detail={"ok": True, "spelling": used})

    def chat(self, body: dict, *, stream: bool = False, timeout: float = 600.0):
        self.chats += 1
        self.last_chat = dict(body)
        text = ((body.get("messages") or [{}])[-1].get("content") or "")
        if stream:
            events = [
                {"choices": [{"delta": {"content": "echo: "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": text[:20]}, "finish_reason": "stop"}]},
            ]
            payload = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            return 200, {"content-type": "text/event-stream"}, [payload.encode()]
        reply = {"choices": [{"message": {"role": "assistant", "content": f"echo: {text}"},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": len(text.split()), "completion_tokens": 2}}
        return 200, {"content-type": "application/json"}, [json.dumps(reply).encode()]

    def slots(self):
        return getattr(self, "n_slots", 1)

    # ---- thinking ahead ------------------------------------------------------------
    # A model that "reasons" in words fact1 fact2 ..., closes its reasoning after `think_after`
    # tokens (if set), and answers with `final_text` on Enter.
    thinking = True
    think_after: int | None = None
    final_text = "more reasoning </think> The answer is 42"

    def thinks(self) -> bool:
        return self.thinking

    def detokenize(self, ids):
        words = {v: k for k, v in self._vocab.items()}
        return " ".join(words.get(i, "?") for i in ids)

    slow_chunks = 0.0                     # seconds a chunk takes, unless aborted (Enter)

    def generate(self, ids, *, n_predict, sampling=None, logit_bias=None, abort=None):
        if self.slow_chunks and abort is not None:
            abort.wait(self.slow_chunks)
        self.generated = getattr(self, "generated", 0)
        self.gen_prompts = getattr(self, "gen_prompts", [])
        self.gen_prompts.append(list(ids))
        words = []
        for _ in range(min(n_predict, 4)):
            self.generated += 1
            if self.think_after is not None and self.generated > self.think_after:
                words.append("</think>")
                break
            words.append(f"fact{self.generated}")
        return {"tokens": self.tokenize(" ".join(words)), "text": " ".join(words), "stop_type": "limit", "timings": {}}

    # VERIFY_PROBE: the next token's probabilities the fake gives ({token: p}); BRANCHES: an answer per slot
    probs_answer = {" yes": 0.9, " no": 0.1}
    final_by_slot: dict = {}

    def next_token_probs(self, ids, *, n_probs=20):
        self.probe_prompts = getattr(self, "probe_prompts", [])
        self.probe_prompts.append(list(ids))
        return dict(self.probs_answer)

    stream_delay_by_slot: dict = {}       # seconds per piece on a slot (a slower branch)
    stream_done: dict = {}                # slot -> when its last answer stream ended (set by the test)

    error_by_slot: dict = {}              # slot -> True: the engine answers that slot's stream with an error
    raise_after_by_slot: dict = {}        # slot -> n: the stream raises after n pieces (a dropped connection)

    def stream(self, ids, *, n_predict, sampling=None):
        self.streamed = list(ids)
        slot = getattr(self, "slot", 0)
        if self.error_by_slot.get(slot):
            yield {"error": "the context is full", "stop": True}
            return
        final = self.final_by_slot.get(slot, self.final_text)
        delay = self.stream_delay_by_slot.get(slot, 0.0)
        pieces = final.replace("</think>", " </think> ").split()
        for i, word in enumerate(pieces):
            if slot in self.raise_after_by_slot and i >= self.raise_after_by_slot[slot]:
                raise ConnectionResetError("the engine went away")
            if delay:
                time.sleep(delay)
            yield {"text": word + " ", "tokens": self.tokenize(word), "stop": False}
        self.stream_done[slot] = time.perf_counter()
        yield {"text": "", "tokens": [], "stop": True, "stop_type": "eos", "timings": {"prompt_n": 1}}

    def models(self):
        return 200, {"object": "list", "data": [{"id": "fake-model"}]}

    def metrics_text(self) -> str | None:
        return self.metrics
