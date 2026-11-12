"""What the agent keeps when the context does not hold it any more.

  * notes: `note(text)` writes down a finding (an address, a name, a value, a decision). Notes are
    never left out, and they are in ~/.mafdet/sessions/<id>/notes.md for the user too.
  * a to-do list: `todo(items)` sets the steps of a long task and which are done; the interface
    shows it. A small model loses the thread of a long task less with the list in front of it.
  * compaction: when leaving out old tool outputs is not enough, the conversation is replaced by a
    summary the model writes of it, followed by the notes, the list and the last exchanges. The
    summary is asked for without tools and without reasoning, on a transcript cut to fit.

The summary is the model's own and can lose things: what must not be lost goes in a note.
"""
from __future__ import annotations

from pathlib import Path

from .tools import Tool

SUMMARY_TOKENS = 700
KEEP_GROUPS = 2             # the last assistant turns (each with its tool results) kept as they are

SUMMARIZE = """You are summarizing a working session between a user and an agent that uses tools, so that the agent \
can go on with only your summary. Write, in plain text and under 400 words:
1. What the user asked for (their words, where short).
2. What has been done and found so far: facts, with exact names, paths, addresses and values.
3. What was tried and did not work.
4. What is left to do, and the very next step.
Only what the transcript says. No preamble."""


class Memory:
    def __init__(self):
        self.notes: list[str] = []
        self.todo: list[dict] = []          # {"task": str, "done": bool}
        self.path: Path | None = None       # the session's notes.md
        self.changed = lambda: None         # the interface redraws the list

    def clear(self, path: Path | None = None):
        self.notes, self.todo, self.path = [], [], path
        self.changed()

    def add_note(self, text: str) -> str:
        text = " ".join(str(text).split())
        if not text:
            return "error: an empty note"
        self.notes.append(text)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"- {text}\n")
        self.changed()
        return f"noted ({len(self.notes)} notes)"

    def set_todo(self, items) -> str:
        todo = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                item = {"task": item}
            if isinstance(item, dict) and str(item.get("task") or "").strip():
                todo.append({"task": " ".join(str(item["task"]).split()), "done": bool(item.get("done"))})
        self.todo = todo
        self.changed()
        return self.todo_text() or "the list is empty"

    def todo_text(self) -> str:
        return "\n".join(f"[{'x' if t['done'] else ' '}] {t['task']}" for t in self.todo)

    def text(self) -> str:
        """Notes and list, as they go after a summary."""
        parts = []
        if self.notes:
            parts.append("Notes written during the work:\n" + "\n".join(f"- {n}" for n in self.notes))
        if self.todo:
            parts.append("To-do list:\n" + self.todo_text())
        return "\n\n".join(parts)


def memory_tools(memory: Memory) -> list[Tool]:
    return [
        Tool("note", "Write down a finding to keep (an address, a name, a value, a decision): notes stay "
                     "when old tool outputs are left out.",
             {"type": "object", "required": ["text"],
              "properties": {"text": {"type": "string", "description": "one fact, complete by itself"}}},
             lambda a: memory.add_note(a["text"])),
        Tool("todo", "Set the to-do list of a task of several steps: all the steps, each done or not. "
                     "Call it again as steps are done.",
             {"type": "object", "required": ["items"],
              "properties": {"items": {"type": "array", "items": {
                  "type": "object", "required": ["task"],
                  "properties": {"task": {"type": "string"}, "done": {"type": "boolean"}}}}}},
             lambda a: memory.set_todo(a["items"])),
    ]


def transcript(messages: list[dict], max_chars: int) -> str:
    """The conversation as text for the summary: tool outputs cut short, and the start of it cut when
    the whole does not fit (the end is what the summary must get right)."""
    rows = []
    for m in messages:
        role, content = m.get("role"), str(m.get("content") or "").strip()
        if role == "system":
            continue
        if role == "user":
            rows.append(f"USER: {content}")
        elif role == "assistant":
            if content:
                rows.append(f"AGENT: {content[:1500]}")
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                rows.append(f"AGENT CALLS {fn.get('name')}({str(fn.get('arguments') or '')[:300]})")
        elif role == "tool":
            rows.append(f"RESULT: {content[:500]}" + ("…" if len(content) > 500 else ""))
    text = "\n".join(rows)
    if len(text) > max_chars:
        text = "[the start of the session is not shown]\n" + text[len(text) - max_chars:]
    return text


def tail_from(messages: list[dict], groups: int = KEEP_GROUPS) -> int:
    """Where the last `groups` assistant turns start: a cut there leaves no tool result without its call."""
    starts = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    return starts[-groups] if len(starts) >= groups else len(messages)


def compact(messages: list[dict], model, memory: Memory, limit_tokens: int, ratio: float) -> list[dict] | None:
    """The conversation as [system, summary + notes + list, last turns]; None when there is too little
    to summarize or the model gives nothing."""
    cut = tail_from(messages)
    old = messages[1:cut]
    if len(old) < 3:
        return None
    budget = int((limit_tokens - SUMMARY_TOKENS) * 0.8 / max(ratio, 0.15))        # characters of transcript
    reply = model.chat([{"role": "system", "content": SUMMARIZE},
                        {"role": "user", "content": transcript(old, max(2000, budget))}],
                       None, max_tokens=SUMMARY_TOKENS, temperature=0.2, think=False)
    summary = (reply.content or "").strip()
    if not summary:
        return None
    last_user = next((str(m.get("content") or "") for m in reversed(old) if m.get("role") == "user"), "")
    text = ("[The earlier part of this session was replaced by this summary, to make room.]\n\n" + summary
            + (f"\n\nThe user's last message, in full:\n{last_user[:3000]}" if last_user else "")
            + ("\n\n" + memory.text() if memory.text() else "")
            + "\n\nGo on from here.")
    return [messages[0], {"role": "user", "content": text}] + messages[cut:]
