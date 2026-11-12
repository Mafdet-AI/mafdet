"""The agent in the terminal (Textual): what `mafdet agent` opens.

A conversation with the model at work in it: you write at the bottom; above, each tool call with
the start of its result, edits as a diff, the answer as Markdown. While the model reasons, its
thoughts are not shown: a cat face blinks next to what a cat would be doing (Mafdet is a feline
goddess), and the reasoning stays folded in a line, to open with Ctrl+O. A call that needs your
approval asks in place: y, a or n.

    Enter           send (while the model works: queued, sent when it is done)
    Shift+Enter     a new line (Ctrl+J where the terminal does not tell them apart)
    Esc             stop the model, or say no to a question
    Shift+Tab       approval: ask -> edits -> all
    Ctrl+O          reasoning and tool outputs in full, or folded
    Up / Down       earlier messages
    Ctrl+C          clear the message, stop the model, or leave
    /help /tools /notes /compact /new /approve <mode> /quit
"""
from __future__ import annotations

import difflib
import json
import random
import threading
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Markdown, Static, TextArea

from ..identity import AMBER
from ..tui.fx import ALARM, BLACK, DEEP, DIM, FAINT, HOT, LINE, PANEL, RAIL, SURFACE, TEXT
from .permissions import MODES

THEME = Theme(name="mafdet-agent", primary=AMBER, secondary=TEXT, accent=HOT, foreground=TEXT, background=BLACK,
              surface=SURFACE, panel=PANEL, success=HOT, warning=AMBER, error=ALARM, dark=True,
              variables={"scrollbar": DEEP, "scrollbar-hover": LINE, "scrollbar-active": DIM,
                         "scrollbar-background": BLACK, "input-cursor-background": HOT,
                         "input-selection-background": "#D4892A55"})

HELP = """Enter sends, Shift+Enter (or Ctrl+J) starts a new line, Esc stops the model.
Shift+Tab changes what runs without asking: ask (writes and commands ask), edits (writes in the folder run), all (commands too).
Ctrl+O shows the reasoning and the tool outputs in full. Up and Down bring back earlier messages.
/tools  the tools the model has      /notes  what the model wrote down      /compact  summarize to make room
/new  a new conversation      /approve ask|edits|all      /quit"""

FOLDED_LINES, OPEN_LINES = 4, 40
REPLAY = 60                 # messages of a resumed session shown again

# What the model is up to while it works, as a cat would put it. Plain ASCII faces: they show in any terminal.
CAT_FACES = ("=^.^=", "=^.^=", "=^.^=", "=^-^=", "=^.^=", "=^.^=", "=^o^=", "=^.^=")
CAT_WORDS = ("Purring", "Kneading", "Making biscuits", "Stalking", "Pouncing", "Prowling", "Grooming",
             "Chasing the red dot", "Loafing", "Tail-flicking", "Whisker-twitching", "Ear-swiveling", "Slow-blinking",
             "Sharpening claws", "Napping on it", "Stretching", "Chirping at birds", "Sniffing around",
             "Sitting in the box", "Pawing at it", "Knocking things off the desk", "Hunting", "Zooming",
             "Chasing its tail", "Watching the fish", "Hiding under the sofa", "Climbing the curtains",
             "Batting at the yarn", "Curling up", "Meowing at the door")
WORD_SECONDS = 5.0          # how long one of them stays


def call_line(name: str, args) -> str:
    """A tool call on one line: the command of a shell call, the short arguments of the others."""
    if not isinstance(args, dict):
        return f"{name} {args}"
    if name == "shell":
        return f"shell  {args.get('command', '')}"
    parts = []
    for k, v in args.items():
        if k in ("content", "old", "new"):
            continue
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={v if len(v) <= 80 else v[:79] + '…'}")
    return f"{name}  {', '.join(parts)}"


def diff_text(old: str, new: str, limit: int = 30) -> Text:
    out, n = Text(), 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        style = {"- ": ALARM, "+ ": HOT, "  ": FAINT}.get(line[:2])
        if style is None:
            continue
        n += 1
        if n > limit:
            out.append("  …\n", style=FAINT)
            break
        out.append("  " + line + "\n", style=style)
    out.rstrip()
    return out


def change_text(name: str, args) -> Text | None:
    """What a write would change, for the call's line and for the question before it runs."""
    if not isinstance(args, dict):
        return None
    if name == "edit_file":
        return diff_text(str(args.get("old", "")), str(args.get("new", "")))
    if name == "write_file":
        return diff_text("", str(args.get("content", "")))
    return None


class Prompt(TextArea):
    """The message box: Enter sends, Shift+Enter breaks the line, Up and Down recall."""

    class Submitted(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def __init__(self, **kw):
        super().__init__(show_line_numbers=False, soft_wrap=True,
                         placeholder="Ask, or say what to do…  (Enter sends, Shift+Enter a new line)", **kw)
        self.sent: list[str] = []
        self.at: int | None = None

    def _recall(self, step: int):
        if not self.sent:
            return
        at = (len(self.sent) if self.at is None else self.at) + step
        if at >= len(self.sent):
            self.at, text = None, ""
        else:
            self.at = max(0, at)
            text = self.sent[self.at]
        self.text = text
        self.move_cursor(self.document.end)

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                if not self.sent or self.sent[-1] != text:
                    self.sent.append(text)
                self.at = None
                self.text = ""
                self.post_message(self.Submitted(text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "up" and self.cursor_location[0] == 0 and (self.at is not None or not self.text):
            event.prevent_default()
            event.stop()
            return self._recall(-1)
        if event.key == "down" and self.at is not None and self.cursor_location[0] == self.document.line_count - 1:
            event.prevent_default()
            event.stop()
            return self._recall(1)
        await super()._on_key(event)


class AgentTUI(App):
    CSS = f"""
    Screen {{ background: $background; }}
    #log {{ height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }}
    #log > Static {{ height: auto; }}
    .welcome {{ border: round {LINE}; padding: 0 1; margin: 0 0 0 0; color: {DIM}; }}
    .user {{ background: {PANEL}; color: {HOT}; margin: 1 0 0 0; padding: 0 1; }}
    .think {{ margin: 1 0 0 0; }}
    #activity {{ display: none; height: 1; margin: 1 2 0 2; }}
    #todo {{ display: none; height: auto; max-height: 10; margin: 1 2 0 2; }}
    .text {{ margin: 1 0 0 0; }}
    Markdown.answer {{ margin: 1 0 0 0; padding: 0; background: transparent; }}
    .tool {{ margin: 1 0 0 0; }}
    .note {{ color: {DIM}; }}
    .err {{ color: {ALARM}; margin: 1 0 0 0; }}
    #ask {{ display: none; height: auto; max-height: 60%; border: round {AMBER}; padding: 0 1; margin: 0 1; }}
    #prompt {{ height: auto; min-height: 3; max-height: 12; border: round {LINE}; margin: 0 1; background: {SURFACE}; }}
    #prompt:focus {{ border: round {AMBER}; }}
    #status {{ height: 1; padding: 0 2; color: {DIM}; background: {RAIL}; }}
    """
    BINDINGS = [Binding("escape", "stop", show=False, priority=True),
                Binding("shift+tab", "cycle_mode", show=False, priority=True),
                Binding("ctrl+o", "toggle_detail", show=False, priority=True),
                Binding("ctrl+c", "interrupt", show=False, priority=True)]

    def __init__(self, make_agent, *, label: str, workdir: str, mode: str = "ask"):
        """`make_agent(emit, ask, mode, fresh)` builds the agent; `fresh` asks for a new session."""
        super().__init__()
        self.make_agent = make_agent
        self.label, self.workdir, self.mode = label, workdir, mode
        self.agent = None
        self.running = False
        self.stopping = False
        self.queue: list[str] = []
        self.kind = ""                        # the block being streamed: "think", "text" or ""
        self.block: Static | None = None
        self.buf = ""
        self.t_block = self.t_turn = 0.0
        self.detail = False
        self.folds: list[tuple[Static, str, str]] = []     # (widget, "think" or "result", what it shows in full)
        self.used = self.limit = 0
        self.tps = 0.0
        self.doing = ""                       # under the cat: "thinking", "writing", or the tool that runs
        self.pieces = 0                       # pieces written this turn, about a token each
        self.word, self.t_word, self.frame = "", 0.0, 0
        self.asking = False
        self.declined = False
        self._answer = ""
        self._answered = threading.Event()

    # ------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="log")
        yield Static(id="todo")
        yield Static(id="activity")
        yield Static(id="ask")
        yield Prompt(id="prompt")
        yield Static(id="status")

    def on_mount(self):
        self.register_theme(THEME)
        self.theme = THEME.name
        self.log_view = self.query_one("#log", VerticalScroll)
        self._new_agent(fresh=False)
        self.query_one(Prompt).focus()
        self.set_interval(0.5, self._status)
        self.set_interval(0.2, self._activity)
        self._status()

    def on_unmount(self):
        if self.agent is not None:
            self.agent.stop.set()
        self._answered.set()

    def _new_agent(self, fresh: bool):
        self.agent = self.make_agent(self._emit, self._ask, self.mode, fresh)
        self.limit = self.agent.context.limit
        self.used = 0
        box = self.agent.toolbox
        resumed = len(self.agent.messages) - 1
        t = Text()
        t.append("MAFDET agent", style=f"bold {AMBER}")
        t.append(f"   {self.label}\n", style=TEXT)
        t.append(f"folder    {self.workdir}\n")
        t.append(f"tools     {len(box.active)}" + (f", {len(box.hidden)} more through find_tools" if box.hidden else "")
                 + "\n")
        t.append(f"session   {self.agent.session.id}" + (f" (resumed, {resumed} messages)" if resumed else "") + "\n")
        t.append("Enter sends · Esc stops · Shift+Tab approval · Ctrl+O reasoning and details · /help", style=FAINT)
        self._put(t, "welcome")
        self.agent.memory.changed = self._memory_changed
        if resumed:
            self._replay(self.agent.messages[1:])
        self._todo()

    def _replay(self, messages: list[dict]):
        """A resumed session on the screen again: what was said and done, without the reasoning."""
        if len(messages) > REPLAY:
            self._put(Text(f"  … {len(messages) - REPLAY} earlier messages not shown", style=FAINT), "note")
        for m in messages[-REPLAY:]:
            role, content = m.get("role"), str(m.get("content") or "").strip()
            if role == "user":
                self._put(Text("> " + content), "user")
            elif role == "assistant":
                if content:
                    self.log_view.mount(Markdown(content, classes="answer"))
                for call in m.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = fn.get("arguments")
                    self._event("tool", {"name": fn.get("name", ""), "args": args})
            elif role == "tool":
                self._event("result", {"text": content})
        self.doing = ""
        self._put(Text("  ⎿ resumed here", style=DIM), "note")

    def _memory_changed(self):
        try:
            self.call_from_thread(self._todo)
        except Exception:                       # called from this thread (a new agent), or the app is closing
            try:
                self._todo()
            except Exception:
                pass

    def _todo(self):
        """The model's to-do list, above the message box while there is one."""
        panel = self.query_one("#todo", Static)
        todo = self.agent.memory.todo if self.agent is not None else []
        panel.display = bool(todo)
        if not todo:
            return
        t = Text()
        done = sum(1 for item in todo if item["done"])
        t.append(f"To do  {done}/{len(todo)}\n", style=f"bold {DIM}")
        current = next((i for i, item in enumerate(todo) if not item["done"]), -1)
        for i, item in enumerate(todo[:8]):
            if item["done"]:
                t.append(f"  [x] {item['task']}\n", style=FAINT)
            else:
                t.append(f"  [ ] {item['task']}\n", style=HOT if i == current else TEXT)
        if len(todo) > 8:
            t.append(f"  … {len(todo) - 8} more\n", style=FAINT)
        t.rstrip()
        panel.update(t)

    def _put(self, content, classes: str) -> Static:
        widget = Static(content, classes=classes)
        self.log_view.mount(widget)
        self.log_view.scroll_end(animate=False)
        return widget

    def _status(self):
        label = self.label if len(self.label) <= 32 else self.label[:31] + "…"
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(label, style=TEXT)
        t.append(f" · {self.used:,}/{self.limit:,} tok · ")
        t.append(self.mode, style=HOT if self.mode != "ask" else TEXT)
        t.append(" ⇧⇥", style=FAINT)
        if self.asking:
            t.append(" · your answer?", style=HOT)
        elif self.stopping:
            t.append(" · stopping…", style=HOT)
        if self.queue:
            t.append(f" · {len(self.queue)} queued", style=HOT)
        if self.tps:
            t.append(f" · {self.tps:.0f} tok/s")
        try:
            self.query_one("#status", Static).update(t)
        except NoMatches:                       # the timer fired while the app is closing
            pass

    def _activity(self):
        """The line above the message box while the model works: a blinking cat and what it is doing."""
        try:
            line = self.query_one("#activity", Static)
        except NoMatches:                       # the timer fired while the app is closing
            return
        show = self.running and not self.asking
        if line.display != show:
            line.display = show
        if not show:
            return
        now = time.monotonic()
        if not self.word or now - self.t_word > WORD_SECONDS:
            self.word = random.choice([w for w in CAT_WORDS if w != self.word])
            self.t_word = now
        self.frame += 1
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(CAT_FACES[self.frame % len(CAT_FACES)] + " ", style=f"bold {AMBER}")
        t.append(("Stopping" if self.stopping else self.word) + "…", style=HOT)
        t.append(f"  ({now - self.t_turn:.0f} s", style=FAINT)
        if self.pieces:
            t.append(f" · {self.pieces} tokens", style=FAINT)
        if self.doing:
            t.append(f" · {self.doing}", style=FAINT)
        t.append(" · esc to stop)", style=FAINT)
        line.update(t)

    # ------------------------------------------------------------ the agent's events (worker thread -> here)

    def _emit(self, kind: str, **d):
        try:
            self.call_from_thread(self._event, kind, d)
        except Exception:                       # the app is closing
            pass

    def _event(self, kind: str, d: dict):
        if kind in ("think", "text"):
            if self.kind != kind:
                self._end_block()
                self.kind, self.buf, self.t_block = kind, "", time.monotonic()
                # The reasoning is not shown as it is written: the cat is. It gets its line when it ends.
                self.block = self._put("", kind) if kind == "text" else None
                self.doing = "thinking" if kind == "think" else "writing"
            self.buf += d["text"]
            self.pieces += 1
            if kind == "text":
                self.block.update(Text(self.buf.lstrip("\n")))
                self.log_view.scroll_end(animate=False)
            return
        self._end_block()
        if kind == "tool":
            t = Text("● ", style=AMBER)
            t.append(call_line(d["name"], d["args"]), style=TEXT)
            self.doing = f"running {d['name']}"
            change = change_text(d["name"], d["args"])
            if change is not None and change.plain:
                t.append("\n")
                t.append_text(change)
            self._put(t, "tool")
        elif kind == "result":
            self.doing = ""
            if self.declined:                   # said already, in one word
                self.declined = False
                return self._status()
            widget = self._put("", "note")
            self.folds.append((widget, "result", d["text"]))
            self._fold(widget, "result", d["text"])
        elif kind == "declined":
            self.declined = True
            self._put(Text("  ⎿ declined", style=DIM), "note")
        elif kind == "left_out":
            self._put(Text(f"  context: {d['n']} old tool outputs left out", style=FAINT), "note")
        elif kind == "step":
            self.used = d["prompt_tokens"] + d["completion_tokens"]
            self.limit = d["limit"]
            if d["seconds"] > 0 and d["completion_tokens"]:
                self.tps = d["completion_tokens"] / d["seconds"]
        elif kind == "compacted":
            self._put(Text(f"  context: the conversation so far was summarized, about {d['before']:,} → "
                           f"{d['after']:,} tokens (notes and to-do list kept)", style=DIM), "note")
            self.used = d["after"]
        elif kind == "stopped":
            self._put(Text("  ⎿ stopped", style=DIM), "note")
        elif kind == "error":
            self._put(Text("! " + d["text"]), "err")
        self._status()

    def _end_block(self):
        """The block being streamed is done: the reasoning folds to a line, the answer becomes Markdown."""
        block, kind, buf = self.block, self.kind, self.buf.strip()
        self.block, self.kind, self.buf = None, "", ""
        if kind == "think":
            if buf:
                full = f"{time.monotonic() - self.t_block:.0f}\n{buf}"
                widget = self._put("", "think")
                self.folds.append((widget, "think", full))
                self._fold(widget, "think", full)
        elif block is not None:
            if buf:
                self.log_view.mount(Markdown(buf, classes="answer"), after=block)
            block.remove()
            self.log_view.scroll_end(animate=False)

    def _fold(self, widget: Static, kind: str, full: str):
        if kind == "think":
            seconds, _, text = full.partition("\n")
            t = Text(f"∴ thought for {seconds} s", style=f"italic {DIM}")
            if self.detail:
                t.append("\n" + text, style=f"italic {FAINT}")
            widget.update(t)
            return
        lines = full.splitlines() or [""]
        show = OPEN_LINES if self.detail else FOLDED_LINES
        t = Text(style=DIM)
        for i, line in enumerate(lines[:show]):
            t.append(("  ⎿ " if i == 0 else "    ") + line[:300] + "\n")
        if len(lines) > show:
            t.append(f"    … {len(lines) - show} more lines" + ("" if self.detail else " (ctrl+o)"), style=FAINT)
        t.rstrip()
        widget.update(t)

    # ------------------------------------------------------------ approvals (asked from the worker thread)

    def _ask(self, tool, args: dict, why: str) -> str:
        self._answer = ""
        self._answered.clear()
        try:
            self.call_from_thread(self._show_ask, tool, args, why)
        except Exception:
            return "n"
        while not self._answered.wait(0.1):
            if self.agent.stop.is_set():
                break
        try:
            self.call_from_thread(self._hide_ask)
        except Exception:
            pass
        return self._answer

    def _show_ask(self, tool, args: dict, why: str):
        t = Text()
        t.append(f"{tool.name}", style=f"bold {AMBER}")
        t.append(f"  {why}\n", style=DIM)
        change = change_text(tool.name, args)
        if tool.name == "shell":
            t.append(str(args.get("command", "")) + "\n", style=TEXT)
        elif change is not None:
            t.append(str(args.get("path", "")) + "\n", style=TEXT)
            t.append_text(change)
            t.append("\n")
        else:
            shown = json.dumps(args, ensure_ascii=False, indent=1)
            t.append((shown if len(shown) <= 1500 else shown[:1500] + "…") + "\n", style=TEXT)
        t.append("Run it?  ", style=TEXT)
        t.append("y", style=f"bold {HOT}")
        t.append(" yes   ")
        t.append("a", style=f"bold {HOT}")
        t.append(f" always for {tool.name}   ")
        t.append("n", style=f"bold {HOT}")
        t.append(" no (esc)")
        ask = self.query_one("#ask", Static)
        ask.update(t)
        ask.display = True
        self.asking = True
        self.query_one(Prompt).disabled = True
        self.set_focus(None)
        self._status()

    def _hide_ask(self):
        self.asking = False
        self.query_one("#ask", Static).display = False
        prompt = self.query_one(Prompt)
        prompt.disabled = False
        prompt.focus()
        self._status()

    def _reply(self, answer: str):
        self._answer = answer
        self._answered.set()

    def on_key(self, event):
        if self.asking and event.key in ("y", "a", "n"):
            event.stop()
            self._reply(event.key)

    # ------------------------------------------------------------ what the user does

    def on_prompt_submitted(self, message: Prompt.Submitted):
        text = message.text
        if text.startswith("/"):
            return self._command(text)
        if self.running:
            self.queue.append(text)
            return self._status()
        self._start(text)

    def _start(self, text: str):
        self.running, self.stopping, self.t_turn = True, False, time.monotonic()
        self.pieces, self.doing, self.word = 0, "", ""
        self._put(Text("> " + text), "user")
        self._status()
        self.run_worker(lambda: self._turn(text), thread=True)

    def _turn(self, text: str):
        try:
            self.agent.run(text)
        except Exception as e:                  # a bug here must not leave the app waiting forever
            self._emit("error", text=f"{type(e).__name__}: {e}")
        try:
            self.call_from_thread(self._turn_done)
        except Exception:
            pass

    def _compact(self):
        try:
            self.agent.compact()                # when it does not, it says why
        except Exception as e:
            self._emit("error", text=f"{type(e).__name__}: {e}")
        try:
            self.call_from_thread(self._turn_done)
        except Exception:
            pass

    def _turn_done(self):
        self._end_block()
        self.running = self.stopping = False
        self._activity()
        if self.queue:
            self._start(self.queue.pop(0))
        self._status()

    def _command(self, text: str):
        name, _, arg = text.partition(" ")
        arg = arg.strip()
        if name in ("/quit", "/exit"):
            return self.exit()
        if name == "/help":
            self._put(Text(HELP, style=DIM), "note")
        elif name == "/tools":
            t = Text(style=DIM)
            for tool in self.agent.toolbox.active.values():
                t.append(f"  {tool.name:<24}", style=TEXT)
                t.append(f"{tool.risk:<6}{tool.source:<8}{tool.description[:90]}\n")
            hidden = len(self.agent.toolbox.hidden)
            t.append(f"  {hidden} more through find_tools" if hidden else "")
            t.rstrip()
            self._put(t, "note")
        elif name == "/notes":
            notes = self.agent.memory.notes
            self._put(Text("\n".join(f"  - {n}" for n in notes) if notes else "  no notes yet", style=DIM), "note")
        elif name == "/compact":
            if self.running:
                self._put(Text("  stop the model first (Esc)", style=DIM), "note")
            else:
                self.running, self.stopping, self.t_turn = True, False, time.monotonic()
                self.pieces, self.doing, self.word = 0, "summarizing", ""
                self.run_worker(self._compact, thread=True)
        elif name == "/approve":
            if arg in MODES:
                self._set_mode(arg)
            else:
                self._put(Text(f"  /approve {' | '.join(MODES)}", style=DIM), "note")
        elif name == "/new":
            if self.running:
                self._put(Text("  stop the model first (Esc)", style=DIM), "note")
            else:
                self.folds.clear()
                self.log_view.remove_children()
                self._new_agent(fresh=True)
        else:
            self._put(Text(f"  no command {name}: /help", style=DIM), "note")
        self._status()

    def _set_mode(self, mode: str):
        self.mode = self.agent.permissions.mode = mode
        self._status()

    def action_cycle_mode(self):
        self._set_mode(MODES[(MODES.index(self.mode) + 1) % len(MODES)])

    def action_toggle_detail(self):
        self.detail = not self.detail
        for widget, kind, full in self.folds:
            self._fold(widget, kind, full)

    def action_stop(self):
        if self.asking:
            self._reply("n")
        elif self.running:
            self.queue.clear()
            self.stopping = True
            self.agent.stop.set()
            self._status()

    def action_interrupt(self):
        prompt = self.query_one(Prompt)
        if prompt.text:
            prompt.text = ""
        elif self.running:
            self.action_stop()
        else:
            self.exit()
