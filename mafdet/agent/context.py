"""The context budget. A local model has 8-16k tokens, and one decompiled function or one listing
of strings can take a third of it: the harness, not the model, keeps the prompt inside the budget.

  * `clip`: a tool's output is cut to its head and tail before the model sees it; the whole of it
    is on disk, and the model is told where, so it can read the part it needs.
  * `Context.fit`: when the prompt would not fit, the oldest tool outputs are replaced by a line.
    That changes the prompt from that point on and the engine reads it again, so it is done in one
    go, down to ROOM (60%) of the budget, and then not for many steps. Compaction aims at the same.

Tokens are estimated from characters, with the ratio the engine reported on the last request.
"""
from __future__ import annotations

import json

STUB = "[output left out to save context"
ROOM = 0.6                      # what is left of the budget after making room: 40% for the steps that follow
# Tool schemas (JSON) on Qwen3's vocabulary, by llama-tokenize: 3.7 characters a token for the agent's own
# 8 tools, 4.0 with 17 of rea's. Prose is about 4 too; the budget starts at 3 to be safe, then follows the engine.
JSON_CHARS_PER_TOKEN = 3.9


class ContextFull(RuntimeError):
    pass


def stub(path: str = "") -> str:
    """What stands for an output left out: where the whole of it is, so the model can get it back."""
    return STUB + (f"; the whole of it is in {path}: read_file it (offset, limit) to get it back]" if path
                   else ": call the tool again if it is needed]")


def json_tokens(obj) -> int:
    """About how many tokens a JSON value (the tools' schemas) takes, before the engine has counted any."""
    return round(len(json.dumps(obj, ensure_ascii=False)) / JSON_CHARS_PER_TOKEN)


def clip(text: str, max_chars: int, where: str = "") -> str:
    """`text` if it fits, else its head and tail around a line saying what is missing and where it is."""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    cut = text.rfind("\n", 0, head)
    head = cut + 1 if cut > head // 2 else head            # on a line boundary when one is near
    left_out = len(text) - head - tail
    note = f"[... {left_out} of {len(text)} characters left out"
    note += f"; the whole of it is in {where}: read_file it with offset and limit, or search it]" if where else "]"
    return text[:head].rstrip("\n") + "\n" + note + "\n" + text[len(text) - tail:]


def head(text: str, max_chars: int, where: str) -> str:
    """The first `max_chars` of an output (on a line boundary when one is near), and where the rest is."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    n = cut if cut > max_chars // 2 else max_chars
    return (text[:n].rstrip("\n") + f"\n[... the rest, {len(text) - n} characters, left out to save context; "
            f"the whole of it is in {where}: read_file it (offset, limit) to get it back]")


class Context:
    def __init__(self, ctx: int, reserve: int, keep_last: int = 4):
        self.limit = max(1024, ctx - reserve)       # tokens the prompt may take
        self.keep_last = keep_last                  # most recent tool outputs never left out
        self.ratio = 1 / 3                          # tokens per character, until the engine says

    @staticmethod
    def chars(messages: list[dict], tools: list[dict] | None) -> int:
        return len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tools or [], ensure_ascii=False))

    def estimate(self, messages: list[dict], tools: list[dict] | None) -> int:
        return int(self.chars(messages, tools) * self.ratio)

    def calibrate(self, messages: list[dict], tools: list[dict] | None, prompt_tokens: int):
        if prompt_tokens:
            self.ratio = min(0.6, max(0.15, prompt_tokens / max(1, self.chars(messages, tools))))

    def fit(self, messages: list[dict], tools: list[dict] | None, paths: dict[str, str] | None = None) -> int:
        """Leave out old tool outputs, in place, until the prompt fits. Returns how many."""
        if self.estimate(messages, tools) <= self.limit:
            return 0
        outputs = [i for i, m in enumerate(messages)
                   if m.get("role") == "tool" and not str(m.get("content", "")).startswith(STUB)]
        old = outputs[:-self.keep_last] if self.keep_last else outputs
        done = 0
        for i in old:
            if self.estimate(messages, tools) <= self.limit * ROOM:
                break
            line = stub((paths or {}).get(messages[i].get("tool_call_id", ""), ""))
            if len(str(messages[i].get("content", ""))) <= len(line):     # "noted (2 notes)": the line is longer
                continue
            messages[i] = dict(messages[i], content=line)
            done += 1
        if self.estimate(messages, tools) > self.limit:
            raise ContextFull("the conversation no longer fits in the context: start a new one (/new), "
                              "or give the model a larger context (--ctx)")
        return done
