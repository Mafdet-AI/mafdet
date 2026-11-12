"""The book of a binary: MAFDET's own record of what each function of it is, on disk and independent
of sessions, in ~/.mafdet/binaries/<first 16 hex digits of the file's sha256>/:

    book.json   per function (by address): the decompiler's name, the given name, a one-line summary,
                an optional comment, confidence, status (pending, done, known, skipped, failed and why),
                callees, size hints, what it cost (seconds, tokens, model calls), and what was written
                back to the decompiler; and the binary's own facts. Written whole, through a temporary
                file and a rename, after every function: a kill loses at most the function in flight.
    book.md     the same for a person, regenerated from it: the functions from the roots down.

The walk (`walk.py`) fills it; an interactive session reads it with the `book` tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .backend import addr_int, default_address

STATUSES = ("pending", "done", "known", "skipped", "failed")


def binaries_dir() -> Path:
    from ..core import config
    path = config.home() / "binaries"
    path.mkdir(exist_ok=True)
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_function(address: str, name: str) -> dict:
    return {"address": address, "orig": name, "name": "", "summary": "", "comment": "", "confidence": "",
            "status": "pending", "reason": "", "callees": None, "outside": [], "flags": {}, "size": None,
            "code_chars": None, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "steps": 0,
            "mirror": {}, "updated": ""}


def has_code(f: dict) -> bool:
    """A function of the binary itself: not an import, not a thunk to one (those have no code to read)."""
    return f.get("status") != "known" or not (f["flags"].get("external") or f["flags"].get("thunk")
                                              or str(f.get("address", "")).startswith("EXTERNAL:"))


def label(f: dict) -> str:
    return f.get("name") or f.get("orig") or f.get("address", "?")


def sort_key(address: str):
    n = addr_int(address)
    return (0, n, "") if n is not None else (1, 0, str(address))


class Book:
    def __init__(self, folder: Path, data: dict | None = None):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "book.json"
        self.data = data if data is not None else self._load()
        self.data.setdefault("version", 1)
        self.data.setdefault("binary", {})
        self.data.setdefault("backend", {})
        self.data.setdefault("runs", [])
        self.data.setdefault("functions", {})
        self._index()

    # ------------------------------------------------------------ on disk

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            raise SystemExit(f"{self.path} cannot be read ({e}): move it away to start the book again.")

    @classmethod
    def for_binary(cls, binary: Path) -> "Book":
        binary = Path(binary).resolve()
        digest = sha256(binary)
        book = cls(binaries_dir() / digest[:16])
        info = book.data["binary"]
        info.update({"path": str(binary), "name": binary.name, "sha256": digest, "size": binary.stat().st_size})
        info.setdefault("started", now())
        return book

    @classmethod
    def find(cls, which: str) -> "Book":
        """A book by its binary's path, or by the start of its sha256."""
        p = Path(which).expanduser()
        if p.is_file():
            folder = binaries_dir() / sha256(p)[:16]
        else:
            key = which.strip().lower()
            found = [d for d in binaries_dir().iterdir() if d.name.startswith(key) and (d / "book.json").is_file()] \
                if len(key) >= 4 and all(c in "0123456789abcdef" for c in key) else []
            if len(found) != 1:
                raise SystemExit(f"No book for {which} in {binaries_dir()}" + (" (more than one matches)" if found else
                                                                                ": walk the binary first (--walk)."))
            folder = found[0]
        if not (folder / "book.json").is_file():
            raise SystemExit(f"No book for {which} yet: walk the binary first (--walk).")
        return cls(folder)

    def save(self, md: bool = False):
        """Atomically: a reader (or a kill) sees the old book or the new one, never half of one."""
        self.data["updated"] = now()
        _atomic(self.path, json.dumps(self.data, indent=1, ensure_ascii=False))
        if md:
            _atomic(self.dir / "book.md", self.markdown())

    # ------------------------------------------------------------ functions

    @property
    def functions(self) -> dict:
        return self.data["functions"]

    def _index(self):
        self.by_int: dict[int, str] = {}
        self.by_name: dict[str, str] = {}
        for a, f in self.functions.items():
            n = addr_int(a)
            if n is not None:
                self.by_int.setdefault(n, a)
            for name in (f.get("orig"), f.get("name")):
                if name:
                    self.by_name.setdefault(name, a)

    def merge(self, listed: list[dict], is_real) -> int:
        """Functions from the backend's listing; new ones are pending, or known when they carry a real
        name. What the book already has stays (after a rename in the decompiler, its listing shows our
        names: they are not real names from the binary). Returns how many were new."""
        new = 0
        for item in listed:
            a = item["address"]
            if a in self.functions:
                continue
            n = addr_int(a)
            if n is not None and n in self.by_int:      # the same address, spelled another way
                continue
            f = new_function(a, item.get("name") or "")
            f["flags"] = {k: item[k] for k in ("external", "thunk", "source") if item.get(k)}
            f["size"] = item.get("size")
            if item.get("external") or is_real(f["orig"]):
                f["status"] = "known"
            self.functions[a] = f
            new += 1
        self._index()
        return new

    def resolve(self, ref: str) -> str | None:
        """A callee as the backend said it (an address, a name, a default name) -> its address in the book."""
        if ref in self.functions:
            return ref
        n = addr_int(ref)
        if n is not None and n in self.by_int:
            return self.by_int[n]
        if ref in self.by_name:
            return self.by_name[ref]
        n = default_address(ref)
        return self.by_int.get(n) if n is not None else None

    def taken(self, but: str = "") -> set[str]:
        """Names in use. An import is listed as MSVCRT.DLL::memset and called `memset` in the code:
        both are taken."""
        out = set()
        for a, f in self.functions.items():
            if a == but:
                continue
            if f["status"] == "done" and f["name"]:
                out.add(f["name"])
            elif f["status"] == "known" and f["orig"]:
                out.update({f["orig"], f["orig"].rsplit("::", 1)[-1]})
        return out

    def unique(self, name: str, address: str) -> str:
        taken = self.taken(but=address)
        if name not in taken:
            return name
        i = 2
        while f"{name}_{i}" in taken:
            i += 1
        return f"{name}_{i}"

    def record(self, address: str, name: str, summary: str, comment: str, confidence: str) -> str:
        f = self.functions[address]
        f.update(name=self.unique(name, address), summary=summary, comment=comment, confidence=confidence,
                 status="done", reason="", updated=now())
        self.by_name.setdefault(f["name"], address)
        return f["name"]

    def counts(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for f in self.functions.values():
            out[f.get("status", "pending")] = out.get(f.get("status", "pending"), 0) + 1
        return out

    def callers(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for a, f in self.functions.items():
            for c in f.get("callees") or []:
                if c != a:
                    out.setdefault(c, []).append(a)
        return out

    def line(self, f: dict, chars: int = 160) -> str:
        """One function in one line: address, name, what it was, the summary."""
        what = f.get("summary") or {"known": "import or symbol" if f["flags"].get("external") else "named in the binary",
                                    "pending": "not analysed yet", "failed": f"not understood ({f.get('reason')})",
                                    "skipped": f"skipped ({f.get('reason')})"}.get(f["status"], "")
        was = f" (was {f['orig']})" if f.get("name") and f["orig"] and f["orig"] != f["name"] else ""
        text = f"{f['address']} {label(f)}{was} — {what}"
        return text if len(text) <= chars else text[:chars - 1] + "…"

    # ------------------------------------------------------------ for a person

    def reach(self, root: str) -> int:
        """How many functions are below this one (each once)."""
        fs, seen, stack = self.functions, {root}, [root]
        while stack:
            for c in fs[stack.pop()].get("callees") or []:
                if c not in seen and c in fs:
                    seen.add(c)
                    stack.append(c)
        return len(seen) - 1

    def roots(self) -> list[str]:
        """Functions nobody calls, the program's top first: the one with the most below it (`entry`, then
        main's tree), not the lowest address (CRT initializers reached only through _initterm's tables came
        first in where.exe's book, 2026-11-12). A real name with code of its own (`entry`) is a root too."""
        called = self.callers()
        found = [a for a, f in self.functions.items()
                 if a not in called and (f["status"] != "known" or has_code(f) and f.get("callees"))]
        return sorted(found, key=lambda a: (-self.reach(a), sort_key(a)))

    def markdown(self, max_depth: int = 24) -> str:
        info, fs = self.data["binary"], self.functions
        c = self.counts()
        out = [f"# {info.get('name', 'binary')}", "",
               f"- path: `{info.get('path', '')}`", f"- sha256: `{info.get('sha256', '')}`",
               f"- backend: {self.data['backend'].get('server', '?')} ({self.data['backend'].get('adapter', '?')})",
               f"- updated: {self.data.get('updated', '')}",
               f"- functions: {len(fs)}: " + ", ".join(f"{n} {s}" for s, n in c.items() if n), "",
               "## From the roots down", "",
               "Each function once, the callees with the most below them first; later calls to it say *(above)*. "
               "The `calls:` line under a function names all it calls; imports and other named functions with "
               "nothing below them are only there.", ""]
        seen: set[str] = set()
        reach: dict[str, int] = {}

        def below(a: str) -> int:
            if a not in reach:
                reach[a] = self.reach(a)
            return reach[a]

        def walk(root: str):
            stack = [(root, 0)]
            while stack:
                a, depth = stack.pop()
                f = fs.get(a)
                if f is None:
                    continue
                pad = "  " * depth
                if a in seen:
                    out.append(f"{pad}- {label(f)} *(above)*")
                    continue
                seen.add(a)
                name = f"**{label(f)}**" if f["status"] == "done" else label(f)
                rest = self.line(f, 400).split(" — ", 1)[1]
                conf = f" [{f['confidence']}]" if f.get("confidence") else ""
                out.append(f"{pad}- {name} `{a}`{conf} — {rest}")
                kids = [k for k in (f.get("callees") or []) if k != a and k in fs]
                # Imports were most of the first lines (GetProcessHeap, SetLastError... *(above)* under every
                # allocator of where.exe, 2026-11-12): one line, by the name the code uses.
                # The functions it calls are named first on that line too: below them the tree goes deep (main's
                # first callee took 200 lines of where.exe's book before its second one showed).
                ends = [k for k in kids if fs[k]["status"] == "known" and not fs[k].get("callees")]
                kids = sorted((k for k in kids if k not in ends), key=lambda k: (-below(k), sort_key(k)))
                calls = list(dict.fromkeys([f"**{label(fs[k])}**" if fs[k]["status"] == "done" else label(fs[k])
                                            for k in kids if len(kids) > 1]
                                           + [label(fs[k]).rsplit("::", 1)[-1] for k in ends]
                                           + list(f.get("outside") or [])))
                if calls:
                    out.append(f"{pad}  - calls: " + ", ".join(calls))
                if depth + 1 >= max_depth and kids:
                    out.append(f"{pad}  - … {len(kids)} more below")
                    continue
                for k in reversed(kids):
                    stack.append((k, depth + 1))

        for r in self.roots():
            walk(r)
        rest = sorted((a for a, f in fs.items() if a not in seen and f["status"] != "known"), key=sort_key)
        for a in rest:                                   # in cycles nobody else calls
            if a not in seen:
                walk(a)
        failed = [f for f in fs.values() if f["status"] in ("failed", "skipped")]
        if failed:
            out += ["", "## Failed and skipped", ""]
            out += [f"- `{f['address']}` {label(f)}: {f['status']}, {f.get('reason', '')}"
                    for f in sorted(failed, key=lambda f: sort_key(f["address"]))]
        out += ["", "## All functions, by address", "", "| address | name | was | status | summary |",
                "|---|---|---|---|---|"]
        for a in sorted(fs, key=sort_key):
            f = fs[a]
            cell = (f.get("summary") or "").replace("|", "\\|")
            out.append(f"| `{a}` | {label(f)} | {f['orig'] if f.get('name') else ''} | {f['status']} | {cell} |")
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------ for the model of a session

    def lookup(self, query: str = "", address: str = "") -> str:
        fs = self.functions
        if address:
            a = self.resolve(address.strip())
            if a is None:
                return f"no function at {address} in the book of {self.data['binary'].get('name')}"
            f = fs[a]
            out = [self.line(f, 400)]
            if f.get("confidence"):
                out[0] += f" [{f['confidence']} confidence]"
            if f.get("comment"):
                out.append(f["comment"])
            kids = [k for k in f.get("callees") or [] if k in fs]
            if kids:
                out.append("calls:\n" + "\n".join("  " + self.line(fs[k]) for k in kids[:40]))
            if f.get("outside"):
                out.append("calls also: " + ", ".join(f["outside"][:30]))
            parents = self.callers().get(a, [])
            if parents:
                out.append("called by:\n" + "\n".join("  " + self.line(fs[p]) for p in parents[:20]))
            return "\n".join(out)
        words = query.lower().split()
        if not words:
            c = self.counts()
            roots = self.roots()[:20]
            return (f"{self.data['binary'].get('name')}: {len(fs)} functions, "
                    + ", ".join(f"{n} {s}" for s, n in c.items() if n)
                    + (".\nRoots (called by nothing):\n" + "\n".join("  " + self.line(fs[r]) for r in roots)
                       if roots else ".") + "\nAsk with a query (words of a name or summary) or an address.")
        hits = [f for f in fs.values() if all(w in self.line(f, 100000).lower() for w in words)]
        hits.sort(key=lambda f: (f["status"] != "done", sort_key(f["address"])))
        if not hits:
            return "nothing in the book matches: try other words"
        more = f"\n[{len(hits) - 40} more: narrow the query]" if len(hits) > 40 else ""
        return "\n".join(self.line(f) for f in hits[:40]) + more


def _atomic(path: Path, text: str):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(20):                   # Windows: a reader holding the file makes replace fail for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def book_tool(book: Book):
    """The book as a read-only tool of an interactive session (added only when a book is in play)."""
    from .tools import Tool
    return Tool("book", f"What MAFDET's walk found about the functions of {book.data['binary'].get('name', 'the binary')}: "
                        "names and one-line summaries. Search by words, or one function by address or name.",
                {"type": "object", "properties": {
                    "query": {"type": "string", "description": "words of a name or summary"},
                    "address": {"type": "string", "description": "one function: its address or name, with its callees"}}},
                lambda a: book.lookup(str(a.get("query") or ""), str(a.get("address") or "")))
