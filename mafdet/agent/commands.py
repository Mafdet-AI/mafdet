"""The agent's slash commands: one table for the menu, the help and what runs.

Typing `/` in the message box opens a menu above it with the commands and what each does, narrowed as
you type; ↑↓ move, Tab completes, Enter runs, Esc closes. A command that takes one of a few values
(`/approve`, `/reasoning`) shows them in the same menu once its name is written. What a command answers
goes into the conversation as a card of its own (tui.py), not as loose lines.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..tui.fx import DIM, FAINT, HOT, TEXT
from .model import LEVELS
from .permissions import MODES


@dataclass(frozen=True)
class Command:
    name: str
    about: str
    choices: tuple[tuple[str, str], ...] = ()      # (value, what it means)


APPROVE = (("ask", "writes, commands and unknown tools ask first"),
           ("edits", "writes inside the folder run by themselves; commands still ask"),
           ("all", "commands too: for a VM or a throwaway folder"))
REASONING = (("auto", "the model's or the provider's own default"),
             ("off", "no reasoning: the fastest, for simple steps"),
             ("low", "a little"),
             ("medium", "a fair amount"),
             ("high", "as much as it takes: the slowest"))
assert tuple(v for v, _ in APPROVE) == tuple(MODES) and tuple(v for v, _ in REASONING) == LEVELS

COMMANDS = (
    Command("model", "another model, on this machine or in the cloud"),
    Command("reasoning", "how much the model reasons before each step", REASONING),
    Command("approve", "what runs without asking you", APPROVE),
    Command("tools", "the tools the model has"),
    Command("notes", "what the model wrote down"),
    Command("compact", "summarize the conversation to make room"),
    Command("new", "start a new conversation"),
    Command("help", "commands and keys"),
    Command("quit", "leave the agent"),
)
BY_NAME = {c.name: c for c in COMMANDS}
ALIASES = {"exit": "quit", "q": "quit", "?": "help", "h": "help"}

KEYS = (("Enter", "send (while the model works: queued)"),
        ("Shift+Enter", "a new line (Ctrl+J where the terminal does not tell them apart)"),
        ("Esc", "stop the model, or say no to a question"),
        ("Shift+Tab", "approval: ask → edits → all"),
        ("Ctrl+O", "reasoning and tool outputs in full, or folded"),
        ("Ctrl+T", "install the tool the message seems to need"),
        ("↑ ↓", "earlier messages"),
        ("Ctrl+C", "clear the message, stop the model, or leave"))


def find(name: str) -> Command | None:
    name = name.lstrip("/").lower()
    return BY_NAME.get(ALIASES.get(name, name))


def suggest(name: str) -> str:
    """The command closest to a mistyped one, or ""."""
    close = difflib.get_close_matches(name.lstrip("/").lower(), list(BY_NAME), n=1, cutoff=0.5)
    return close[0] if close else ""


def matches(text: str) -> list[Option]:
    """The menu's entries for what is in the message box: commands while the name is being written, the
    values of a command that takes some once it is written; [] when there is nothing to offer."""
    if not text.startswith("/") or "\n" in text:
        return []
    word, space, rest = text[1:].partition(" ")
    if not space:
        word = word.lower()
        starts = [c for c in COMMANDS if c.name.startswith(word)]
        inside = [c for c in COMMANDS if word and word in c.name and c not in starts]
        return [_command_option(c) for c in starts + inside]
    command = find(word)
    if command is None or not command.choices or " " in rest.strip():
        return []
    rest = rest.strip().lower()
    return [_choice_option(command, value, about) for value, about in command.choices if value.startswith(rest)]


def _command_option(c: Command) -> Option:
    t = Text()
    t.append(f"/{c.name:<11}", style=f"bold {HOT}")
    t.append(c.about, style=TEXT)
    if c.choices:
        t.append(f"   {' · '.join(v for v, _ in c.choices)}", style=FAINT)
    return Option(t, id=f"cmd:{c.name}")


def _choice_option(c: Command, value: str, about: str) -> Option:
    t = Text()
    t.append(f"/{c.name} ", style=DIM)
    t.append(f"{value:<8}", style=f"bold {HOT}")
    t.append(about, style=TEXT)
    return Option(t, id=f"arg:{c.name}:{value}")


class SlashMenu(OptionList):
    """The menu above the message box. The box keeps the focus: the app moves the highlight for it."""

    can_focus = False

    def show(self, text: str) -> bool:
        options = matches(text)
        self.clear_options()
        if options:
            self.add_options(options)
            self.highlighted = 0
        self.display = bool(options)
        return self.display

    def hide(self):
        self.display = False

    def chosen(self) -> tuple[str, str, str] | None:
        """("cmd", name, "") or ("arg", name, value) for the highlighted entry."""
        if not self.display or self.highlighted is None:
            return None
        kind, name, *value = (self.get_option_at_index(self.highlighted).id or "").split(":")
        return kind, name, value[0] if value else ""

    def move(self, step: int):
        if self.option_count:
            self.highlighted = ((self.highlighted or 0) + step) % self.option_count
