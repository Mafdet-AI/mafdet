"""The tools the agent may install by itself, from a catalog that ships with MAFDET (`kit.json`), never
from a name or an address the model makes up.

A local model reading hostile content (the strings of a sample, a web page, a tool's output) must not be
able to turn "install X from this link" into code running on the machine, and a small model picking
packages by name gets them wrong (look-alike names are a known attack). So:

  - only ids of the catalog: versions pinned, every download checked against the digest written in it
    (a sha256 from the publisher, or the npm registry's sha512 integrity), a mismatch installs nothing;
  - the user is asked before anything is downloaded, with what, how big, from where, under which licence
    and why the model wants it; ids in `auto_install` of ~/.mafdet/agent.json go without asking, unless the
    tool's name appears in a tool output of the same turn (then the request may come from the content);
  - everything goes under ~/.mafdet/tools/<id>/<version>: archives unpacked there, npm packages with their
    own prefix, Python packages in their own venv; the system's PATH and its programs are left alone;
  - a tool with an MCP server is written into ~/.mafdet/mcp.json (an entry of the user's with the same
    name is kept as it is) and started in the session at once.

`need_tool` is the model's way in; `mafdet agent --kit` lists the catalog, `--install ID` installs by hand.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .tools import Tool

CATALOG = Path(__file__).with_name("kit.json")
CHUNK = 1 << 20
TIMEOUT = 60                    # seconds without a byte before a download fails
RUN_TIMEOUT = 1800              # npm or pip, seconds
AGENT = "mafdet"                # the User-Agent of the downloads: nothing about the user
PLACEHOLDER = re.compile(r"\{(exe|dir\??|bin|script|data):([\w.-]+)(?::([\w.-]+))?\}")


class KitError(RuntimeError):
    pass


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    system = "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"
    return f"{system}-{arch}"


def tools_dir() -> Path:
    from ..core import config
    path = config.home() / "tools"
    path.mkdir(exist_ok=True)
    return path


def _urlopen(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=TIMEOUT)


def java_major(java: str) -> int | None:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major            # "1.8.0" is Java 8


class Kit:
    def __init__(self, catalog: dict | None = None, root: Path | None = None, plat: str | None = None,
                 fetch=None, java=None):
        self.catalog = catalog if catalog is not None else json.loads(CATALOG.read_text(encoding="utf-8"))["tools"]
        self.root = Path(root) if root else tools_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.plat = plat or platform_key()
        self.windows = self.plat.startswith("windows")
        self.fetch = fetch or _urlopen
        self.java = java                                # tests: a function -> the system's Java major, or None
        self.registry = self.root / "installed.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------ what there is

    def installed(self) -> dict:
        try:
            data = json.loads(self.registry.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _record(self, tool_id: str, info: dict):
        data = self.installed()
        data[tool_id] = info
        tmp = self.registry.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.registry)

    def entry(self, tool_id: str) -> dict:
        e = self.catalog.get(tool_id)
        if not isinstance(e, dict):
            raise KitError(f"{tool_id!r} is not in MAFDET's catalog: {', '.join(self.ids())}")
        return e

    def file(self, tool_id: str) -> dict | None:
        files = self.entry(tool_id).get("files") or {}
        return files.get(self.plat) or files.get("any")

    def ids(self) -> list[str]:
        """The catalog's tools that exist for this platform (and so do their needs)."""
        out = []
        for i in self.catalog:
            try:
                if all(self.file(x) for x in self.closure(i)):
                    out.append(i)
            except KitError:
                pass
        return out

    def closure(self, tool_id: str) -> list[str]:
        """The tool and what it needs, needs first."""
        out: list[str] = []

        def visit(i: str, path: tuple):
            if i in path:
                raise KitError(f"the catalog's needs go round: {' -> '.join(path + (i,))}")
            for n in self.entry(i).get("needs") or []:
                visit(n, path + (i,))
            if i not in out:
                out.append(i)
        visit(tool_id, ())
        return out

    def is_installed(self, tool_id: str) -> bool:
        info = self.installed().get(tool_id)
        return bool(info) and info.get("version") == self.entry(tool_id)["version"] and Path(info["dir"]).is_dir()

    def found(self, tool_id: str) -> str:
        """A tool the machine has already, as the catalog asks (the JDK: a Java new enough). "" when not."""
        need = (self.entry(tool_id).get("detect") or {}).get("java")
        if not need:
            return ""
        if self.java is not None:
            major = self.java()
            where = "on this machine"
        else:
            home = os.environ.get("JAVA_HOME")
            exe = (Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if home else None
            java = str(exe) if exe and exe.is_file() else shutil.which("java")
            major = java_major(java) if java else None
            where = f"Java {major} at {java}"
        return where if major and major >= need else ""

    def status(self, tool_id: str) -> str:
        if self.is_installed(tool_id):
            return "installed"
        if self.found(tool_id):
            return "found on this machine"
        return ""

    def plan(self, tool_id: str) -> list[str]:
        """What must be installed for this tool, needs first: nothing already installed or found."""
        return [i for i in self.closure(tool_id) if not self.is_installed(i) and not self.found(i)]

    def describe(self, ids: list[str]) -> str:
        """What a person is asked to approve."""
        lines, total = [], 0
        for i in ids:
            e, f = self.entry(i), self.file(i) or {}
            total += int(f.get("mb") or 0)
            host = re.sub(r"^https?://([^/]+)/.*$", r"\1", f.get("url", ""))
            lines.append(f"  {e['name']} {e['version']} (~{f.get('mb', '?')} MB, from {host}, {e.get('license', '?')}): "
                         f"{e.get('what', '')}")
        return (f"MAFDET would download and install, into {self.root}:\n" + "\n".join(lines)
                + f"\n  about {total} MB in all; each file checked against the digest in MAFDET's catalog")

    # ------------------------------------------------------------ installing

    def install(self, tool_id: str, say=lambda text: None, stop: threading.Event | None = None) -> list[str]:
        """Install the tool and what it needs. Returns the ids installed now."""
        with self._lock:
            done = []
            for i in self.plan(tool_id):
                say(f"installing {self.entry(i)['name']} {self.entry(i)['version']}...")
                t0 = time.monotonic()
                self._install_one(i, say, stop)
                say(f"  {self.entry(i)['name']} installed in {time.monotonic() - t0:.0f} s")
                done.append(i)
            return done

    def _install_one(self, tool_id: str, say, stop):
        e, f = self.entry(tool_id), self.file(tool_id)
        if not f:
            raise KitError(f"{e['name']} has no download for {self.plat} in MAFDET's catalog")
        dest = self.root / tool_id / e["version"]
        if dest.exists():                               # a copy not in the registry: left by a failed install
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        download = self._download(f, say, stop)
        info = {"version": e["version"], "kind": e["kind"], "dir": str(dest), "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
                "url": f["url"]}
        kind = e["kind"]
        if kind == "archive":
            self._unpack(download, dest, f.get("root") or "")
        elif kind == "npm":
            info["bin"] = self._npm(tool_id, download, dest, stop)
        elif kind == "pip":
            info["scripts"] = self._pip(tool_id, download, dest, stop)
        else:
            raise KitError(f"unknown kind {kind!r} for {tool_id} in the catalog")
        self._record(tool_id, info)
        try:
            download.unlink()                           # a 500 MB zip has nothing more to give
        except OSError:
            pass

    def _download(self, f: dict, say, stop) -> Path:
        if f.get("sha256"):
            algo, expected = "sha256", f["sha256"].lower()
        elif str(f.get("integrity", "")).startswith("sha512-"):
            algo, expected = "sha512", base64.b64decode(f["integrity"][7:]).hex()
        else:
            raise KitError("the catalog gives no digest for this file: not downloaded")
        folder = self.root / ".downloads"
        folder.mkdir(exist_ok=True)
        name = re.sub(r"[^\w.+-]", "_", urllib.request.unquote(f["url"].rsplit("/", 1)[-1])) or "download"
        path = folder / name
        if path.is_file() and _digest(path, algo) == expected:      # kept from an install that failed later
            return path
        limit = int(float(f.get("mb") or 2000) * 1.5 + 64) << 20
        tmp = path.with_name(path.name + ".part")
        h, total, shown = hashlib.new(algo), 0, 0
        try:
            with self.fetch(f["url"]) as response, open(tmp, "wb") as out:
                while True:
                    if stop is not None and stop.is_set():
                        raise KitError("stopped")
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise KitError(f"{f['url']} is larger than the catalog says ({total >> 20} MB): stopped")
                    h.update(chunk)
                    out.write(chunk)
                    if total - shown >= 50 << 20:
                        shown = total
                        say(f"  {total >> 20} MB")
        except KitError:
            tmp.unlink(missing_ok=True)
            raise
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise KitError(f"download of {f['url']} failed: {e}") from None
        if h.hexdigest() != expected:
            tmp.unlink(missing_ok=True)
            raise KitError(f"{name}: its {algo} is not the one in MAFDET's catalog (got {h.hexdigest()[:16]}…, "
                           f"expected {expected[:16]}…): nothing installed")
        os.replace(tmp, path)
        return path

    def _unpack(self, archive: Path, dest: Path, root: str):
        partial = dest.with_name(dest.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        base = partial.resolve()
        try:
            if archive.name.endswith(".zip"):
                with zipfile.ZipFile(archive) as z:
                    for m in z.infolist():
                        target = (partial / m.filename).resolve()
                        if base != target and base not in target.parents:
                            raise KitError(f"{archive.name}: {m.filename!r} would land outside its folder")
                    z.extractall(partial)
                    if os.name != "nt":                 # zip keeps the executable bit only in its own field
                        for m in z.infolist():
                            mode = m.external_attr >> 16
                            if mode & 0o111:
                                os.chmod(partial / m.filename, mode & 0o777)
            else:
                with tarfile.open(archive) as t:
                    if hasattr(tarfile, "data_filter"):
                        t.extractall(partial, filter="data")
                    else:
                        for m in t.getmembers():
                            target = (partial / m.name).resolve()
                            if base != target and base not in target.parents or m.issym() or m.islnk():
                                raise KitError(f"{archive.name}: {m.name!r} would land outside its folder")
                        t.extractall(partial)
            children = list(partial.iterdir())
            top = children[0] if len(children) == 1 and children[0].is_dir() else partial
            top = top / root if root else top
            if not top.is_dir():
                raise KitError(f"{archive.name}: no {root or 'folder'} inside")
            shutil.move(str(top), str(dest))
        except (zipfile.BadZipFile, tarfile.TarError) as e:
            raise KitError(f"{archive.name} cannot be unpacked: {e}") from None
        finally:
            shutil.rmtree(partial, ignore_errors=True)

    def _run(self, tool_id: str, cmd: list[str], stop, env: dict | None = None):
        log = self.root / "logs"
        log.mkdir(exist_ok=True)
        path = log / f"{tool_id}.log"
        with open(path, "w", encoding="utf-8", errors="replace") as out:
            out.write(" ".join(cmd) + "\n\n")
            out.flush()
            try:
                proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        env={**os.environ, **(env or {})},
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except OSError as e:
                raise KitError(f"cannot run {cmd[0]}: {e}") from None
            t0 = time.monotonic()
            while proc.poll() is None:
                if stop is not None and stop.is_set() or time.monotonic() - t0 > RUN_TIMEOUT:
                    proc.kill()
                    proc.wait()
                    raise KitError("stopped" if stop is not None and stop.is_set() else f"{cmd[0]} took too long")
                time.sleep(0.2)
        if proc.returncode:
            tail = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
            raise KitError(f"{Path(cmd[0]).name} failed ({proc.returncode}); the end of {path}:\n" + "\n".join(tail))

    def _npm(self, tool_id: str, tgz: Path, dest: Path, stop) -> str:
        """The verified tarball installed with its dependencies under its own prefix, by the catalog's Node.
        Returns the path of the package's command (a .js file, run with that Node)."""
        e = self.entry(tool_id)
        node = self.path("exe", "node")
        node_dir = Path(self.installed()["node"]["dir"])
        cli = node_dir / ("node_modules/npm/bin/npm-cli.js" if self.windows else "lib/node_modules/npm/bin/npm-cli.js")
        partial = dest.with_name(dest.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        try:
            self._run(tool_id, [node, str(cli), "install", "--prefix", str(partial), "--no-fund", "--no-audit",
                                "--omit=dev", "--no-update-notifier", str(tgz)], stop,
                      env={"PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")})
            pkg = partial / "node_modules" / e["package"] / "package.json"
            meta = json.loads(pkg.read_text(encoding="utf-8"))
            bins = meta.get("bin")
            rel = bins if isinstance(bins, str) else (bins or {}).get(e.get("bin") or e["package"])
            if not rel:
                raise KitError(f"{e['package']} has no command named {e.get('bin')}")
            shutil.move(str(partial), str(dest))
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        return str(dest / "node_modules" / e["package"] / rel)

    def _pip(self, tool_id: str, file: Path, dest: Path, stop) -> str:
        """A venv of its own (made in place: a venv does not survive a move), the verified file installed
        into it with its dependencies. Returns the folder of its commands."""
        try:
            self._run(tool_id, [sys.executable, "-m", "venv", str(dest)], stop)
            scripts = dest / ("Scripts" if self.windows else "bin")
            py = scripts / ("python.exe" if self.windows else "python")
            self._run(tool_id, [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                                str(file)], stop)
        except KitError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return str(scripts)

    # ------------------------------------------------------------ using what is installed

    def path(self, what: str, tool_id: str, name: str = "", optional: bool = False) -> str | None:
        info = self.installed().get(tool_id) if self.is_installed(tool_id) else None
        if what == "data":
            p = self.root / "data" / tool_id
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
        if info is None:
            if optional:
                return None
            raise KitError(f"{tool_id} is not installed")
        if what == "dir":
            return info["dir"]
        if what == "exe":
            exe = self.entry(tool_id).get("exe") or {}
            return str(Path(info["dir"]) / exe["windows" if self.windows else "other"])
        if what == "bin":
            return info["bin"]
        if what == "script":
            return str(Path(info["scripts"]) / (name + (".exe" if self.windows else "")))
        raise KitError(f"unknown placeholder {what}")

    def resolve(self, text: str) -> str | None:
        """`{exe:node}`, `{dir:ghidra}`, `{dir?:jdk}` (None when not installed), `{bin:rea}`,
        `{script:pyghidra-mcp:pyghidra-mcp}`, `{data:id}` -> paths."""
        missing = False

        def sub(m):
            nonlocal missing
            what, tool_id, name = m.group(1), m.group(2), m.group(3) or ""
            value = self.path(what.rstrip("?"), tool_id, name, optional=what.endswith("?"))
            if value is None:
                missing = True
                return ""
            return value
        out = PLACEHOLDER.sub(sub, text)
        return None if missing else out

    def mcp_entry(self, tool_id: str) -> dict | None:
        """The tool's server as an entry of mcp.json, its paths filled in."""
        m = self.entry(tool_id).get("mcp")
        if not m:
            return None
        from .mcp import PROFILES
        env = {k: v for k, v in ((k, self.resolve(v)) for k, v in (m.get("env") or {}).items()) if v is not None}
        conf = {"command": self.resolve(m["command"]), "args": [self.resolve(a) or "" for a in m.get("args") or []]}
        if env:
            conf["env"] = env
        tools = list(PROFILES.get(m["profile"], [])) if m.get("profile") else []
        tools += [t for t in m.get("tools") or [] if t not in tools]
        if tools:
            conf["tools"] = tools
        if m.get("allow"):
            conf["allow"] = list(m["allow"])
        conf["_kit"] = tool_id
        conf["_note"] = (f"installed by MAFDET from its catalog: {self.entry(tool_id)['name']} {self.entry(tool_id)['version']}"
                         + (f"; {m['_note']}" if m.get("_note") else ""))
        return conf

    def register(self, tool_id: str) -> tuple[str, dict, bool]:
        """The server into ~/.mafdet/mcp.json. An entry of the same name the user wrote is kept as it is.
        Returns (its name, the entry in use, whether it is MAFDET's)."""
        from ..core import config
        m = self.entry(tool_id).get("mcp") or {}
        name = m["name"]
        data = config.read_json("mcp.json")
        servers = data.setdefault("mcpServers", {})
        theirs = servers.get(name)
        if isinstance(theirs, dict) and theirs.get("command") and theirs.get("_kit") != tool_id:
            return name, theirs, False
        conf = self.mcp_entry(tool_id)
        servers[name] = conf
        config.write_json("mcp.json", data)
        return name, conf, True

    def configured(self, tool_id: str) -> dict | None:
        """The tool's server as mcp.json has it already (the user's own, or one installed before)."""
        from .mcp import configured
        m = self.entry(tool_id).get("mcp")
        conf = configured().get(m["name"]) if m else None
        if not isinstance(conf, dict) or not conf.get("command"):
            return None
        if conf.get("_kit") == tool_id and not self.is_installed(tool_id):
            return None                                  # ours, but the files are gone
        return conf

    def listing(self) -> str:
        rows = []
        for i in self.catalog:
            e = self.entry(i)
            f = self.file(i)
            state = self.status(i) or ("not for this platform" if not f else "")
            if e.get("mcp") and self.configured(i) and state != "installed":
                state = "configured in mcp.json"
            rows.append(f"  {i:<20} {e['version']:<11} ~{(f or {}).get('mb', '?'):>4} MB  {state:<22} {e.get('what', '')}")
        return f"MAFDET's catalog ({self.plat}), installed into {self.root}:\n" + "\n".join(rows)


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- while the user types

def suggest(kit: Kit, text: str, attached=()) -> dict | None:
    """The catalog's tool a message being written seems to need, by the words of the entry's `tasks`
    (English and Italian, file extensions, http://): {"id", "name", "words", "state", "mb"}, or None. No model,
    no GPU: it runs at every keystroke. A tool whose server is on already is not suggested."""
    low = " " + text.lower() + " "
    best, best_score, words = None, 0, []
    for tool_id in kit.ids():
        e = kit.entry(tool_id)
        if not e.get("tasks") or (e.get("mcp") or {}).get("name") in attached:
            continue
        hits = []
        for word in (w.strip().lower() for w in e["tasks"].split(",")):
            if not word:
                continue
            if word.startswith(".") or word.endswith("://"):
                found = re.search(re.escape(word) + (r"(?![\w])" if word.startswith(".") else ""), low)
            else:
                found = re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", low)
            if found:
                hits.append(word)
        if len(hits) > best_score:
            best, best_score, words = tool_id, len(hits), hits
    if best is None:
        return None
    plan = kit.plan(best)
    state = ("start" if kit.configured(best) or not plan else "install")
    mb = sum(int((kit.file(i) or {}).get("mb") or 0) for i in plan) if state == "install" else 0
    return {"id": best, "name": kit.entry(best)["name"], "words": words, "state": state, "mb": mb}


# ---------------------------------------------------------------- the model's tool

def mentioned(kit: Kit, tool_id: str, turn) -> bool:
    """Does a tool output of this turn name the tool? Then a request to install it may come from the
    content being analysed, not from the user's task."""
    e = kit.entry(tool_id)
    names = {tool_id, e.get("name", ""), e.get("package", ""), (e.get("mcp") or {}).get("name", "")}
    rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in names if n) + r")(?![\w-])", re.I)
    return any(m.get("role") == "tool" and rx.search(str(m.get("content") or "")) for m in turn or ())


class NeedTool:
    """`need_tool(id, why)`: install from the catalog (asked first), then start its MCP server in the
    session and add its tools, or say where its commands are."""

    def __init__(self, kit: Kit, box, servers: list, auto=(), stop: threading.Event | None = None, log_dir=None):
        self.kit = kit
        self.box = box
        self.servers = servers
        self.auto = {str(a) for a in auto or ()}
        self.stop = stop
        self.log_dir = log_dir
        self._run_lock = threading.Lock()               # the model and the person (a key) may ask at once

    def tool(self) -> Tool | None:
        ids = self.kit.ids()
        if not ids:
            return None
        lines = []
        for i in ids:
            e = self.kit.entry(i)
            if not e.get("mcp") and not e.get("scripts"):
                continue                                # a dependency only (node, the JDK, Ghidra)
            state = self.kit.status(i) or ("configured" if self.kit.configured(i) else "")
            lines.append(f"{i}: {e.get('what', '')}" + (f" [{state}]" if state else ""))
        t = Tool("need_tool",
                 "Install a tool from MAFDET's catalog and start it, when the task needs one you do not have; "
                 "the user is asked first. The catalog:\n" + "\n".join(lines),
                 {"type": "object", "required": ["id", "why"], "properties": {
                     "id": {"type": "string", "enum": [i for i in ids if self.kit.entry(i).get("mcp")
                                                       or self.kit.entry(i).get("scripts")]},
                     "why": {"type": "string", "description": "what in the task needs it, one line"}}},
                 self.run, "install")
        t.assess = self.assess
        return t

    def _attached(self, tool_id: str) -> bool:
        m = self.kit.entry(tool_id).get("mcp")
        return bool(m) and any(getattr(s, "name", "") == m["name"] for s in self.servers)

    def assess(self, args: dict, turn=()) -> tuple[str, str]:
        """(risk, what to tell the person): "read" runs without asking."""
        tool_id = str(args.get("id") or "")
        try:
            self.kit.entry(tool_id)
        except KitError:
            return "read", ""                           # run() says what is wrong
        if self._attached(tool_id):
            return "read", ""
        plan = self.kit.plan(tool_id)
        if not plan or self.kit.configured(tool_id):
            return "read", ""                           # nothing to download: start what is there
        text = self.kit.describe(plan) + f"\n  the model's reason: {str(args.get('why') or '')[:300]}"
        if tool_id in self.auto and not mentioned(self.kit, tool_id, turn):
            return "read", ""
        if mentioned(self.kit, tool_id, turn):
            text += ("\n  careful: a tool output of this turn names it, so the request may come from the content "
                     "being analysed rather than from your task")
        return "install", text

    def attached_names(self) -> set[str]:
        return {getattr(s, "name", "") for s in self.servers}

    def run(self, args: dict, say=None) -> str:
        with self._run_lock:
            return self._run(args, say)

    def _run(self, args: dict, say=None) -> str:
        tool_id = str(args.get("id") or "")
        kit = self.kit
        try:
            e = kit.entry(tool_id)
        except KitError as err:
            return f"error: {err}"
        if self._attached(tool_id):
            return f"{e['name']} is on already: its tools are in your list (or through find_tools)."
        notes = []
        try:
            if not kit.configured(tool_id):
                done = kit.install(tool_id, say=(lambda t: (notes.append(t), say(t))) if say else notes.append,
                                   stop=self.stop)
                if done:
                    notes = [f"installed: {', '.join(kit.entry(i)['name'] + ' ' + kit.entry(i)['version'] for i in done)}"]
        except KitError as err:
            return f"error: {e['name']} was not installed: {err}"
        out = notes[:]
        if e.get("requires"):
            out.append(f"it needs {e['requires']}; MAFDET does not install that")
        if e.get("scripts"):
            folder = kit.installed()[tool_id]["scripts"]
            out.append(f"its commands, for the shell (full paths, they are not on PATH): "
                       + ", ".join(str(Path(folder) / (s + (".exe" if kit.windows else ""))) for s in e["scripts"]))
        if e.get("mcp"):
            out.append(self._start(tool_id))
        return "\n".join(out)

    def _start(self, tool_id: str) -> str:
        from . import mcp
        conf = self.kit.configured(tool_id)
        if conf is None or conf.get("_kit") == tool_id:     # ours: written again, as the catalog says now
            name, conf, mine = self.kit.register(tool_id)
        else:
            mine = False
        name = self.kit.entry(tool_id)["mcp"]["name"]
        log = mcp.log_path(Path(self.log_dir), name) if self.log_dir else None
        try:
            server = mcp.McpServer(name, conf["command"], conf.get("args") or [], conf.get("env"), conf.get("cwd"),
                                   log=log)
        except Exception as err:
            return f"the server {name} did not start: {err}" + (f" (its log: {log})" if log else "")
        self.servers.append(server)
        before = set(self.box.active) | set(self.box.hidden)
        patterns = [str(p) for p in conf.get("tools") or []] or None
        self.box.add_server(server, patterns, [str(p) for p in conf.get("allow") or []])
        on = [n for n in self.box.active if n not in before and n != "find_tools"]
        off = [n for n in self.box.hidden if n not in before]
        text = (f"the MCP server {name} is on ({'written into' if mine else 'as configured in'} mcp.json): "
                f"{len(on)} tools in your list from the next step: {', '.join(on[:30])}"
                + (f"; {len(off)} more through find_tools" if off else ""))
        if server.instructions:
            text += f"\nAbout its tools: {mcp._clip(server.instructions, 600)}"
        return text


def run_cli(args) -> int:
    """`mafdet agent --kit` and `--install ID`."""
    kit = Kit()
    if getattr(args, "kit", False):
        print(kit.listing())
        return 0
    tool_id = args.install
    try:
        plan = kit.plan(tool_id)
    except KitError as e:
        raise SystemExit(str(e))
    if plan:
        print(kit.describe(plan))
        try:
            answer = input("  install? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer[:1] != "y":
            print("nothing installed")
            return 1
        try:
            kit.install(tool_id, say=print)
        except KitError as e:
            print(f"! {e}", file=sys.stderr)
            return 1
    else:
        print(f"{kit.entry(tool_id)['name']} {kit.entry(tool_id)['version']}: already here ({kit.status(tool_id)})")
    if kit.entry(tool_id).get("mcp"):
        name, conf, mine = kit.register(tool_id)
        print(f"MCP server {name}: " + ("written into mcp.json" if mine else "mcp.json has one of yours by that "
                                                                              "name, kept as it is")
              + f"; use it with --mcp {name}")
    if kit.entry(tool_id).get("scripts"):
        folder = Path(kit.installed()[tool_id]["scripts"])
        print("commands: " + ", ".join(str(folder / (s + (".exe" if kit.windows else "")))
                                       for s in kit.entry(tool_id)["scripts"]))
    return 0
