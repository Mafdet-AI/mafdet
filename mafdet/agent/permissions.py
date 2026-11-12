"""Who decides whether a tool call runs. The model does not: a small model gets commands wrong
more often than a large one, and an abliterated one will not stop by itself.

  ask     (default) reading inside the working folder runs; writing, the shell and any tool that
          is not read-only ask first
  edits   writing inside the working folder runs too
  all     everything inside the working folder runs, the shell included: for a VM or a throwaway
          folder, since a shell command is not confined to it

A call that reaches outside the working folder always asks. "Always" for a tool lasts the session.
An install (`need_tool`, risk "install") asks in every mode, and "always" counts for that call only:
`kit.py` says which installs go without asking.
"""
from __future__ import annotations

from typing import Callable

from .tools import Tool

MODES = ("ask", "edits", "all")


class Permissions:
    def __init__(self, mode: str = "ask", ask: Callable[[Tool, dict, str], str] | None = None):
        if mode not in MODES:
            raise ValueError(f"approval mode must be one of {MODES}")
        self.mode = mode
        self.ask = ask                      # (tool, args, why) -> "y" once, "a" always for this tool, anything else no
        self.always: set[str] = set()

    def allowed(self, tool: Tool, args: dict, turn=()) -> bool:
        risk, said = tool.risk, ""
        if tool.assess is not None:
            try:
                risk, said = tool.assess(args, turn)
            except Exception as e:          # never a reason to run without asking
                risk, said = "install", f"(could not tell what it would do: {e})"
        if risk == "install":
            if self.ask is None:
                return False
            return (self.ask(tool, args, said or "installs software") or "").strip().lower()[:1] in ("y", "a")
        try:
            outside = bool(tool.outside(args))
        except Exception:                   # arguments the tool cannot make sense of: let a person look
            outside = True
        if outside:
            why = "outside the working folder"
        elif risk == "read" or self.mode == "all" or tool.name in self.always:
            return True
        elif risk == "write" and self.mode == "edits":
            return True
        else:
            why = {"write": "writes a file", "exec": "runs a command"}.get(risk, risk)
            if tool.source != "mafdet":
                why = f"a tool of {tool.source}, not marked read-only"
        if self.ask is None:
            return False
        answer = (self.ask(tool, args, why) or "").strip().lower()[:1]
        if answer == "a" and not outside:
            self.always.add(tool.name)
        return answer in ("y", "a")
