"""A session on disk, in ~/.mafdet/sessions/<id>/:
    transcript.jsonl    every message as it entered the conversation (tool outputs as the model saw them)
    notes.md            the model's notes, one line each, as they are written
    todo.md             the model's to-do list as it was last set ("- [x] step" when done)
    outputs/            the tool outputs in full, for the model to read the parts that were cut
    outputs.jsonl       which tool call each of them belongs to
and next to the sessions, mcp-<name>.log: what each MCP server wrote on its standard error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def sessions_dir() -> Path:
    from ..core import config
    path = config.home() / "sessions"
    path.mkdir(exist_ok=True)
    return path


class Session:
    def __init__(self, path: Path | None = None):
        if path is None:
            stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
            path = sessions_dir() / stamp
            while path.exists():
                n += 1
                path = sessions_dir() / f"{stamp}-{n}"
        self.dir = Path(path)
        (self.dir / "outputs").mkdir(parents=True, exist_ok=True)
        self.id = self.dir.name
        self._n = len(list((self.dir / "outputs").iterdir()))

    @classmethod
    def find(cls, which: str) -> "Session":
        """A session by id, or the most recent one ("last")."""
        if which == "last":
            all_ = sorted(p for p in sessions_dir().iterdir() if (p / "transcript.jsonl").is_file())
            if not all_:
                raise SystemExit("No session to resume yet.")
            return cls(all_[-1])
        path = sessions_dir() / which
        if not (path / "transcript.jsonl").is_file():
            raise SystemExit(f"No session {which} in {sessions_dir()}.")
        return cls(path)

    def log(self, message: dict):
        with open(self.dir / "transcript.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def reset(self, messages: list[dict]):
        """The conversation was compacted: from here on it is `messages` (the lines before stay as a record)."""
        self.log({"reset": True})
        for m in messages:
            self.log(m)

    def notes(self) -> list[str]:
        try:
            lines = (self.dir / "notes.md").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line[2:] for line in lines if line.startswith("- ")]

    def todo(self) -> list[dict]:
        try:
            lines = (self.dir / "todo.md").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [{"task": line[6:], "done": line[3] in "xX"} for line in lines
                if line[:3] == "- [" and line[4:6] == "] " and line[6:].strip()]

    def messages(self) -> list[dict]:
        """The conversation so far, without a tail left half done (tool calls with no result)."""
        out = []
        try:
            with open(self.dir / "transcript.jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        break
                    if m.get("reset"):
                        out = []
                    else:
                        out.append(m)
        except OSError:
            return []
        out = [m for m in out if m.get("role") != "system"]
        answered = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
        for i, m in enumerate(out):
            if any(c.get("id") not in answered for c in m.get("tool_calls") or []):
                return out[:i]
        return out

    def save_output(self, tool: str, text: str, call_id: str = "") -> str:
        self._n += 1
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool)[:40]
        path = self.dir / "outputs" / f"{self._n:03d}-{name}.txt"
        path.write_text(text, encoding="utf-8", newline="")
        if call_id:                                 # which call it belongs to, for a resumed session
            with open(self.dir / "outputs.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": call_id, "path": str(path)}) + "\n")
        return str(path)

    def output_paths(self) -> dict[str, str]:
        """tool_call_id -> the file with that call's whole output."""
        out = {}
        try:
            with open(self.dir / "outputs.jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        out[row["id"]] = row["path"]
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            pass
        return out
