"""The agent's shell: one PowerShell (or sh) that stays open for the session, and commands left
running in the background.

A shell started again for every command forgets where it was: a `cd`, a variable, an activated
environment last one call. Here the commands of a session run in the same shell, one after the
other. Each is written to a file and dot-sourced, so it runs in the session's scope whatever is in
it (several lines, quotes, blocks); a marker printed after it says it has ended, with the exit code
and the folder the shell is now in. A command that runs past its timeout, or that the user stops,
takes the shell with it: the next one starts a new shell, in the working folder, and says so.

A command that must stay up (a server, a debugger waiting, a long build) is started with
`background`: it gets a number, its output goes to a file, and `job` reads the end of it or stops it.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAX_OUTPUT = 400_000            # characters of one command kept in memory; the loop cuts what the model reads
JOB_TAIL = 6000                 # characters of a job's output `job` returns


def kill_tree(proc: subprocess.Popen):
    """The process and what it started: PowerShell's children outlive it otherwise."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.kill()
    except OSError:
        pass


def _argv(command: str | None = None) -> list[str]:
    if os.name == "nt":
        base = ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
        return base + [command if command is not None else "-"]
    return ["sh", "-c", command] if command is not None else ["sh"]


class Shell:
    def __init__(self, workdir: Path, scratch: Path, stop: threading.Event | None = None):
        self.workdir = Path(workdir)
        self.scratch = Path(scratch)            # where the command files and the jobs' outputs go
        self.stop = stop
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.cwd = str(self.workdir)
        self.restarted = False                  # the last shell was lost: say it with the next result
        self.jobs: dict[int, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ the session's shell

    def _start(self):
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(_argv(), cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        threading.Thread(target=self._read, args=(self.proc, self.lines), daemon=True).start()
        self.cwd = str(self.workdir)
        if os.name == "nt":
            self._send("[Console]::OutputEncoding = [Text.Encoding]::UTF8")

    @staticmethod
    def _read(proc: subprocess.Popen, lines: queue.Queue):
        for raw in proc.stdout:
            lines.put(raw.decode("utf-8", "replace").replace("\r\n", "\n"))
        lines.put(None)                         # the shell is gone

    def _send(self, line: str):
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def run(self, command: str, timeout: float = 60.0) -> str:
        """Run `command` in the session's shell and return what it printed."""
        with self._lock:
            return self._run(command, timeout)

    def _run(self, command: str, timeout: float) -> str:
        note = ""
        if self.proc is None or self.proc.poll() is not None:
            if self.proc is not None or self.restarted:
                note = (f"[a new shell: the one before was stopped, so its folder and variables are gone; "
                        f"now in {self.workdir}]\n")
            self._start()
            self.restarted = False
        mark = f"<<mafdet-{uuid.uuid4().hex}>>"
        if os.name == "nt":
            script = self.scratch / "command.ps1"
            script.write_text(command, encoding="utf-8-sig")        # the BOM: PowerShell 5 reads it as UTF-8
            lines = ["$global:LASTEXITCODE = 0", f". '{script}'",
                     f"\"{mark} $LASTEXITCODE $((Get-Location).Path)\""]
        else:
            script = self.scratch / "command.sh"
            script.write_text(command, encoding="utf-8", newline="\n")
            lines = [f". '{script}'", f"echo \"{mark} $? $(pwd)\""]
        try:
            for line in lines:
                self._send(line)
        except OSError:
            self.restarted = True
            return note + "error: the shell did not take the command; call again"
        out, size, end, why = [], 0, time.monotonic() + timeout, ""
        while True:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.stop is not None and self.stop.is_set():
                    why = "error: stopped by the user"
                elif time.monotonic() > end:
                    why = (f"error: still running after {timeout:.0f} s, stopped (a longer `timeout`, up to 600; "
                           "or `background` for something that must stay up)")
                if why:
                    break
                continue
            if line is None:
                why = "[the shell ended]"
                break
            if mark in line:
                before, _, tail = line.partition(mark)
                if before.strip():
                    out.append(before)
                code, _, cwd = tail.strip().partition(" ")
                text = "".join(out).strip()
                moved = cwd and cwd != self.cwd
                self.cwd = cwd or self.cwd
                return (note + (text or "[no output]")
                        + (f"\n[exit code {code}]" if code not in ("", "0") else "")
                        + (f"\n[now in {cwd}]" if moved else ""))
            if size < MAX_OUTPUT:
                out.append(line)
                size += len(line)
        kill_tree(self.proc)
        self.restarted = True
        text = "".join(out).strip()
        return note + (text + "\n" if text else "") + why

    # ------------------------------------------------------------ background jobs

    def start_job(self, command: str) -> str:
        n = len(self.jobs) + 1
        (self.scratch / "jobs").mkdir(parents=True, exist_ok=True)
        path = self.scratch / "jobs" / f"{n}.log"
        log = open(path, "wb")
        cwd = self.cwd if os.path.isdir(self.cwd) else str(self.workdir)
        proc = subprocess.Popen(_argv(command), cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.jobs[n] = {"proc": proc, "log": log, "path": path, "command": command, "t0": time.monotonic()}
        time.sleep(0.5)                             # a command that fails at once says so here
        return f"job {n} started in {cwd}\n" + self.job(n)

    def _state(self, job: dict) -> str:
        code = job["proc"].poll()
        return "running" if code is None else f"ended, exit code {code}"

    def job(self, n: int | None = None, action: str = "output") -> str:
        """The jobs, the end of one's output, or one stopped."""
        if n is None:
            return "\n".join(f"job {i}: {self._state(j)}, {time.monotonic() - j['t0']:.0f} s: {j['command'][:120]}"
                             for i, j in self.jobs.items()) or "no job started"
        job = self.jobs.get(int(n))
        if job is None:
            return f"error: no job {n}" + (f" (there: {', '.join(map(str, self.jobs))})" if self.jobs else "")
        if action == "stop":
            kill_tree(job["proc"])
            try:
                job["proc"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        job["log"].flush()
        try:
            data = job["path"].read_bytes()
        except OSError:
            data = b""
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        tail = text[-JOB_TAIL:]
        return (f"job {n}: {self._state(job)}\n"
                + (f"[... the first {len(text) - len(tail)} characters are in {job['path']}]\n" if len(text) > len(tail) else "")
                + (tail.strip() or "[no output yet]"))

    def close(self):
        for job in self.jobs.values():
            kill_tree(job["proc"])
            try:
                job["log"].close()
            except OSError:
                pass
        if self.proc is not None:
            kill_tree(self.proc)
