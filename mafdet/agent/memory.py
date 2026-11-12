"""What the agent keeps when the context does not hold it any more.

  * notes: `note(text)` writes down a finding (an address, a name, a value, a decision). Notes are
    never left out, and they are in ~/.mafdet/sessions/<id>/notes.md for the user too.
  * a to-do list: `todo(items)` sets the steps of a long task and which are done; the interface
    shows it. A small model loses the thread of a long task less with the list in front of it.
    It is written whole to todo.md, next to notes.md, each time it is set: a resumed session
    (`--resume`) finds it there as it was, done steps still done, compacted or not.
  * compaction: when leaving out old tool outputs is not enough, the conversation is replaced by a
    summary the model writes of it, followed by the user's last message, the notes, the list and the
    last turns with their tool outputs reduced to where they are on disk (`loop.Agent.compact`). The
    summary is asked for without tools and without reasoning, on a transcript cut to fit.

The summary is the model's own and can lose things: what must not be lost goes in a note.
"""
from __future__ import annotations

from pathlib import Path

from .tools import Tool

SUMMARY_TOKENS = 700
KEEP_GROUPS = 2             # the last assistant turns (each with its tool results) kept, their outputs reduced

# The first real summaries (Qwen3-14B, 2026-11-12) began by copying the transcript's lines ("USER: ...
# AGENT CALLS ...") and one said "no notes recorded" while there were notes: hence the headings, the
# instruction repeated after the transcript, and the word on the notes.
SUMMARIZE = """You write the working memory of an agent that uses tools. Its conversation is about to be \
replaced by what you write, and it will go on from your text alone. Plain text, under 350 words, these four parts:
Task: what the user asked for (their words, where short).
Established: the facts found so far, each with its exact names, paths, addresses and values.
Done: the steps taken, and what was tried and did not work.
Left: what is still to do, and the very next step.
Write it in your own words: do not copy the transcript's lines (USER, AGENT, RESULT). The agent's notes and \
to-do list are added after your text as they are: do not repeat them, and do not say whether there are any. \
Only what the transcript shows."""


def summary_request(transcript_text: str) -> str:
    """The transcript, then what to do with it again: after a long transcript a small model forgets the
    instruction at the top and goes on copying it."""
    return ("The transcript to summarize:\n\n<transcript>\n" + transcript_text + "\n</transcript>\n\n"
            "Now write the agent's working memory of the transcript above in the four parts "
            "(Task, Established, Done, Left), in your own words.")


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
        if self.path is not None:
            self.path.with_name("todo.md").write_text(
                "".join(f"- [{'x' if t['done'] else ' '}] {t['task']}\n" for t in todo), encoding="utf-8")
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
                     "Call it again as steps are done: a step is done once you have seen its result.",
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
    """Where the last `groups` assistant turns start (as many as there are, when fewer): a cut there leaves
    no tool result without its call."""
    starts = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    return starts[-min(groups, len(starts))] if starts else len(messages)


def summarize(old: list[dict], model, limit_tokens: int, ratio: float) -> str | None:
    """The model's summary of `old`: "" when there is nothing to summarize (no turn of the agent in it),
    None when the model gives nothing. A ModelError goes to the caller."""
    if not any(m.get("role") == "assistant" for m in old):
        return ""
    budget = int((limit_tokens - SUMMARY_TOKENS) * 0.8 / max(ratio, 0.15))        # characters of transcript
    reply = model.chat([{"role": "system", "content": SUMMARIZE},
                        {"role": "user", "content": summary_request(transcript(old, max(2000, budget)))}],
                       None, max_tokens=SUMMARY_TOKENS, temperature=0.2, think=False)
    return (reply.content or "").strip() or None


SUMMARY_HEAD = "[The earlier part of this session was replaced by this summary, to make room.]"
LEFT_OUT_HEAD = "[Tool outputs of this session were left out to make room: each placeholder says which file has the whole of it.]"
USER_HEAD = "\n\nThe user's last message:\n"
GO_ON = "\n\nGo on from here."


def summary_message(summary: str, last_user: str, memory: Memory) -> str:
    """What stands for the earlier part of the conversation: the summary, the notes, the list, and the
    user's last message (when it is not among the turns kept), last, where `request_in` finds it again."""
    text = SUMMARY_HEAD + "\n\n" + summary if summary else LEFT_OUT_HEAD
    if memory.text():
        text += "\n\n" + memory.text()
    if last_user:
        text += USER_HEAD + last_user
    return text + GO_ON


def request_in(text: str) -> str:
    """The user's message in a message: the one quoted in a summary message, or the text itself. (The
    second real compaction of a session quoted the first summary as "the user's last message".)"""
    if text.startswith((SUMMARY_HEAD, LEFT_OUT_HEAD)) and USER_HEAD in text and text.endswith(GO_ON):
        return text[text.rfind(USER_HEAD) + len(USER_HEAD):-len(GO_ON)]
    return text


def is_summary(text: str) -> bool:
    return text.startswith((SUMMARY_HEAD, LEFT_OUT_HEAD))
