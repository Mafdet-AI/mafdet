"""The agent's own tools: files and the shell (`shell.py`), with short schemas: every tool is
read again with every prompt, and Open WebUI's thirty cost 5.8k tokens (research/LOG.md). Notes
and the to-do list are in `memory.py`, the web in `web.py`.

Each tool says how risky it is ("read", "write", "exec") and whether a call reaches outside the
working folder; `permissions.py` decides from that. Everything else (decompilers, debuggers,
devices) comes from MCP servers, see `mcp.py`.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .shell import Shell

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
MAX_SEARCH_FILE = 2_000_000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict], str]
    risk: str = "read"                                       # read, write or exec
    outside: Callable[[dict], bool] = lambda args: False     # does this call leave the working folder?
    source: str = "mafdet"
    # (args, the messages of this turn) -> (risk, what to tell the person): a tool whose risk depends on the
    # call (need_tool: nothing to download, or a download to approve). None: `risk`, always.
    assess: Callable | None = None

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def _params(required: list[str], **props) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def builtin_tools(workdir: Path, read_roots: tuple[Path, ...] = (), stop=None, shell: Shell | None = None) -> list[Tool]:
    """The file tools and the shell, on `workdir`. Reads inside `read_roots` (the session's saved outputs)
    count as inside. `stop` (a threading.Event) ends a shell command that is still running; `shell` is the
    session's shell (one is made, and closed at exit, when none is given)."""
    workdir = workdir.resolve()
    roots = (workdir,) + tuple(Path(r).resolve() for r in read_roots)

    def resolve(path: str) -> Path:
        p = Path(os.path.expanduser(path or "."))
        return (p if p.is_absolute() else workdir / p).resolve()

    def inside(path: str, among=roots) -> bool:
        p = resolve(path)
        return any(p == r or r in p.parents for r in among)

    def read_file(a: dict) -> str:
        p = resolve(a["path"])
        offset, limit = max(1, int(a.get("offset") or 1)), max(1, int(a.get("limit") or 200))
        with open(p, "rb") as f:
            head = f.read(4096)
            if b"\0" in head:                                # a binary: a hex dump from byte `offset`
                f.seek(offset - 1)
                data = f.read(min(limit, 64) * 16)
                rows = [f"{offset - 1 + i:08x}  {data[i:i + 16].hex(' '):<47}  "
                        + "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i + 16])
                        for i in range(0, len(data), 16)]
                return (f"binary file, {p.stat().st_size} bytes; bytes from {offset} "
                        "(offset counts bytes, limit rows of 16):\n" + "\n".join(rows))
            f.seek(0)
            lines = f.read().decode("utf-8", "replace").splitlines()
        part = lines[offset - 1:offset - 1 + limit]
        out = "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(part))
        more = len(lines) - (offset - 1 + len(part))
        return out + (f"\n[{more} more lines: offset {offset + len(part)}]" if more > 0 else "") or "[empty file]"

    def write_file(a: dict) -> str:
        p = resolve(a["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a["content"], encoding="utf-8", newline="")
        return f"wrote {len(a['content'])} characters to {p}"

    def edit_file(a: dict) -> str:
        p = resolve(a["path"])
        text = p.read_bytes().decode("utf-8")
        n = text.count(a["old"])
        if n != 1:
            return (f"error: `old` is in the file {n} times, it must be there exactly once"
                    + (": copy it from read_file, without the line numbers" if n == 0 else ": add lines around it"))
        p.write_bytes(text.replace(a["old"], a["new"]).encode("utf-8"))
        return f"edited {p}"

    def search(a: dict) -> str:
        base = resolve(a.get("path") or ".")
        glob = a.get("glob") or "*"
        pattern = a.get("pattern") or ""
        try:
            rx = re.compile(pattern, re.IGNORECASE) if pattern else None
        except re.error as e:
            return f"error: not a regular expression: {e}"
        files = [base] if base.is_file() else (
            Path(d) / f for d, dirs, names in os.walk(base)
            if not dirs.__setitem__(slice(None), sorted(x for x in dirs if x not in SKIP_DIRS))
            for f in sorted(names) if fnmatch.fnmatch(f, glob))
        out: list[str] = []
        for f in files:
            if len(out) >= 100:
                out.append("[more matches: narrow the pattern, the glob or the path]")
                break
            rel = os.path.relpath(f, workdir) if inside(str(f), (workdir,)) else str(f)
            if rx is None:
                out.append(rel)
                continue
            try:
                if f.stat().st_size > MAX_SEARCH_FILE:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{rel}:{n}: {line.strip()[:200]}")
                    if len(out) >= 100:
                        break
        return "\n".join(out) or "no match"

    if shell is None:
        import atexit
        import tempfile
        shell = Shell(workdir, Path(tempfile.mkdtemp(prefix="mafdet-shell-")), stop)
        atexit.register(shell.close)

    def run_shell(a: dict) -> str:
        if a.get("background"):
            return shell.start_job(a["command"])
        return shell.run(a["command"], min(600, max(1, int(a.get("timeout") or 60))))

    def job(a: dict) -> str:
        return shell.job(a.get("id"), "stop" if a.get("stop") else "output")

    def out(key: str):
        return lambda a: not inside(a.get(key) or ".")

    def out_write(a: dict) -> bool:
        return not inside(a.get("path") or ".", (workdir,))

    sh = "PowerShell" if os.name == "nt" else "sh"
    return [
        Tool("read_file", "Read a text file with line numbers; a binary file as a hex dump.",
             _params(["path"], path=_str("the file"), offset=_int("first line (binary: first byte), from 1"),
                     limit=_int("lines to read, default 200")), read_file, "read", out("path")),
        Tool("write_file", "Create a file, or replace all of its content.",
             _params(["path", "content"], path=_str("the file"), content=_str("its whole content")),
             write_file, "write", out_write),
        Tool("edit_file", "Replace one piece of a text file: `old` must be in it exactly once.",
             _params(["path", "old", "new"], path=_str("the file"), old=_str("the text to replace, exactly"),
                     new=_str("what replaces it")), edit_file, "write", out_write),
        Tool("search", "Find files by name, or lines by regular expression, under a folder.",
             _params([], pattern=_str("regular expression for the lines; empty lists the files"),
                     path=_str("folder or file, default the working folder"),
                     glob=_str("file names to look at, e.g. *.c")), search, "read", out("path")),
        Tool("shell", f"Run a {sh} command and return its output. The shell stays open: the folder and the "
                      "variables of one command are there for the next.",
             _params(["command"], command=_str("the command"), timeout=_int("seconds, default 60"),
                     background={"type": "boolean", "description": "true for something that must stay up (a "
                                                                   "server, a debugger): it becomes a job"}),
             run_shell, "exec"),
        Tool("job", "The background jobs; with an id, the end of that job's output.",
             _params([], id=_int("the job's number"),
                     stop={"type": "boolean", "description": "true to stop that job"}), job, "read"),
    ]
