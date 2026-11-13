r"""Record the real `mafdet agent` window at work, for the clips of the video.

The interface is the real one (mafdet/agent/tui.py, its banner, its status line, its approvals), run
headless by Textual with a model that follows a script: the tools run for real, on a sample folder made
for the occasion (C:\samples\crackme, removed at the end). A screenshot is taken about every 0.1 s and the moments that matter are noted, so the
video can put each one on a beat.

    .venv/Scripts/python video/tools/capture_tui.py        (from the repository's root)

Writes video/public/tui/<clip>/NNNN.svg, video/public/tui/manifest.json and video/src/tui.json; then run
video/tools/svg_to_png.py to turn the screenshots into the PNGs the video shows.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import shutil
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "video" / "public" / "tui"
HOME = Path(tempfile.mkdtemp(prefix="mafdet-capture-home-"))
os.environ["MAFDET_HOME"] = str(HOME)             # no session of the real user touched, none left behind
sys.path.insert(0, str(ROOT))

from mafdet.agent import mcp                       # noqa: E402
from mafdet.agent.loop import Agent                # noqa: E402
from mafdet.agent.model import Reply               # noqa: E402
from mafdet.agent.permissions import Permissions   # noqa: E402
from mafdet.agent.store import Session             # noqa: E402
from mafdet.agent.tools import builtin_tools       # noqa: E402
from mafdet.agent.tui import AgentTUI              # noqa: E402

SIZE = (100, 58)                                   # columns, lines: the banner with the wordmark beside it
LABEL = "Qwen3-14B-Q4_K_M"
SAMPLE = Path(r"C:\samples\crackme")              # the sample folder the tools work in (removed at the end)
QUIP = "I read while you type. I see the typos and say nothing. Mostly."
SNAP = 0.09                                        # seconds between screenshots

CHECK = '''def check(key: str) -> bool:
    """16 characters or more, digits and capitals."""
    ok = all(c.isdigit() or c.isupper() for c in key)
    return ok and len(key) > 16


if __name__ == "__main__":
    import sys
    print("accepted" if check(sys.argv[1]) else "rejected")
'''
FIXED = CHECK.replace("len(key) > 16", "len(key) >= 16")


class Clock:
    def __init__(self):
        self.t0 = time.monotonic()
        self.marks: dict[str, float] = {}

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def mark(self, name: str):
        self.marks.setdefault(name, self.now())


class Scripted:
    """A model that streams what it is told to, at a human-looking pace, and marks when each step starts and
    ends."""

    def __init__(self, steps: list[dict], clock: Clock):
        self.steps, self.clock = list(steps), clock

    def chat(self, messages, tools=None, *, max_tokens=4096, temperature=0.6, think=True, on_delta=None,
             should_stop=None) -> Reply:
        step = self.steps.pop(0)
        self.clock.mark(f"{step['name']}:start")
        reply = Reply()
        for kind, text, pace in (("think", step.get("think", ""), 0.05), ("text", step.get("text", ""), 0.035)):
            for word in text.split(" ") if text else []:
                piece = word + " "
                if kind == "think":
                    reply.reasoning += piece
                else:
                    reply.content += piece
                if on_delta:
                    on_delta(kind, piece)
                time.sleep(pace)
        reply.content = reply.content.strip()
        for i, (name, args) in enumerate(step.get("calls", [])):
            reply.tool_calls.append({"id": f"{step['name']}-{i}", "name": name, "arguments": json.dumps(args)})
        reply.prompt_tokens, reply.completion_tokens = step.get("prompt", 2100), max(12, len(reply.reasoning) // 4)
        self.clock.mark(f"{step['name']}:end")
        return reply


class FakeAhead:
    """What the window reads of thinking ahead: how many tokens it has reasoned. The real one needs
    llama-server; here the count follows the typing, in the real 32-token chunks, to the measured 273."""

    def __init__(self):
        self.reasoned, self.paused, self.agent = 0, False, None

    def typed(self, text: str):
        pass

    def close(self):
        pass


def make_app(steps: list[dict], clock: Clock, mode: str, folder: Path) -> AgentTUI:
    stop = threading.Event()
    box = mcp.Toolbox(builtin_tools(folder, stop=stop))
    model = Scripted(steps, clock)

    def make(emit, ask, mode=mode, fresh=False):
        return Agent(model, box, Permissions(mode, ask), Session(), system="You are MAFDET's agent.", emit=emit,
                     stop=stop, ctx=16384)
    app = AgentTUI(make, label=LABEL, workdir=str(SAMPLE), mode=mode)
    app.title = "mafdet agent"
    app.quip = QUIP
    return app


async def record(app: AgentTUI, clip: str, clock: Clock, script) -> dict:
    """Run `script(pilot)` while taking screenshots; returns the clip's manifest."""
    folder = OUT / clip
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.svg"):
        old.unlink()
    frames: list[dict] = []
    done = asyncio.Event()

    async def shoot():
        while not done.is_set():
            n = len(frames)
            (folder / f"{n:04d}.svg").write_text(app.export_screenshot(), encoding="utf-8")
            frames.append({"file": f"tui/{clip}/{n:04d}.png", "t": clock.now(), "kitten": app.kitten % 64})
            await asyncio.sleep(SNAP)

    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.6)                      # mounted, fonts and banner drawn
        clock.t0 = time.monotonic()
        clock.marks.clear()
        shooter = asyncio.create_task(shoot())
        try:
            await script(pilot, app)
        finally:
            done.set()
            await shooter
    return {"frames": frames, "marks": clock.marks, "duration": frames[-1]["t"] if frames else 0}


async def type_slowly(pilot, app, text: str, pace: float, ahead: FakeAhead | None = None, clock: Clock | None = None):
    prompt = app.query_one("#prompt")
    for i, ch in enumerate(text):
        prompt.insert(ch)
        if ahead is not None and ch == " ":
            ahead.reasoned = min(273, ahead.reasoned + 32)
            app._status()
        await pilot.pause(pace)
    if ahead is not None:
        ahead.reasoned = 273
        app._status()
    if clock:
        clock.mark("typed")


# ---------------------------------------------------------------- the clips

async def clip_banner(folder: Path) -> dict:
    """The window as it opens: the cheetah, the wordmark, the model; the paw taps the ball."""
    clock = Clock()
    app = make_app([], clock, "ask", folder)

    async def script(pilot, app):
        await pilot.pause(8.4)                      # one loop of the banner (64 frames at 8 fps)
    return await record(app, "banner", clock, script)


async def clip_ahead(folder: Path) -> dict:
    """A request typed while the model already reasons on it: the status line counts the tokens."""
    clock = Clock()
    steps = [{"name": "first", "think": "From my notes: open crackme.exe in the decompiler and find the "
              "comparison that decides the key.", "calls": [("search", {"pattern": "def check", "path": "."})]},
             {"name": "second", "think": "check.py holds it.", "text": "The key is checked in check.py."}]
    app = make_app(steps, clock, "ask", folder)
    ahead = FakeAhead()
    app.ahead = ahead

    async def script(pilot, app):
        await pilot.pause(0.5)
        clock.mark("type")
        await type_slowly(pilot, app, "find what check.py accepts as a key", 0.085, ahead, clock)
        await pilot.pause(0.9)
        clock.mark("enter")
        await pilot.press("enter")
        await pilot.pause(3.2)
    return await record(app, "ahead", clock, script)


async def clip_turn(folder: Path) -> dict:
    """A whole turn: the cat at work, read_file, an edit shown as a diff, the answer."""
    clock = Clock()
    steps = [
        {"name": "read", "think": "The user says check.py has a bug. Read it before changing anything.",
         "calls": [("read_file", {"path": "check.py"})]},
        {"name": "edit", "think": "Line 4 compares with > instead of >=: a key of exactly 16 characters is "
         "rejected. Fix that comparison only.",
         "calls": [("edit_file", {"path": "check.py", "old": "len(key) > 16", "new": "len(key) >= 16"})]},
        {"name": "answer", "think": "Done. Say what was wrong.",
         "text": "Fixed. `check()` rejected keys of exactly 16 characters: line 4 compared with `>` "
                 "instead of `>=`. A key like `ABCD1234EFGH5678` passes now."},
    ]
    app = make_app(steps, clock, "edits", folder)

    async def script(pilot, app):
        await pilot.pause(0.4)
        await type_slowly(pilot, app, "fix the bug in check.py", 0.03)
        clock.mark("enter")
        await pilot.press("enter")
        for _ in range(200):
            await pilot.pause(0.1)
            if not app.running:
                break
        clock.mark("done")
        await pilot.pause(1.2)
    return await record(app, "turn", clock, script)


async def clip_approve(folder: Path) -> dict:
    """Shift+Tab through the approval modes, then a command that asks first: y, and it runs."""
    clock = Clock()
    steps = [
        {"name": "run", "think": "Run it with the key and see what it prints.",
         "calls": [("shell", {"command": "python check.py ABCD1234EFGH5678"})]},
        {"name": "answer", "think": "It printed accepted.", "text": "It prints **accepted**: the key passes now."},
    ]
    app = make_app(steps, clock, "ask", folder)

    async def script(pilot, app):
        await pilot.pause(0.5)
        for mode in ("edits", "all", "ask"):
            await pilot.press("shift+tab")
            clock.mark(f"mode:{mode}")
            await pilot.pause(0.8)
        await type_slowly(pilot, app, "run check.py on ABCD1234EFGH5678", 0.03)
        clock.mark("enter")
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.05)
            if app.asking:
                break
        clock.mark("asked")
        await pilot.pause(1.6)
        clock.mark("yes")
        await pilot.press("y")
        for _ in range(200):
            await pilot.pause(0.1)
            if not app.running:
                break
        clock.mark("done")
        await pilot.pause(1.2)
    return await record(app, "approve", clock, script)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"size": {"columns": SIZE[0], "lines": SIZE[1]}, "clips": {}}
    for name, clip, content in (("banner", clip_banner, CHECK), ("ahead", clip_ahead, CHECK),
                                ("turn", clip_turn, CHECK), ("approve", clip_approve, FIXED)):
        SAMPLE.mkdir(parents=True, exist_ok=True)
        (SAMPLE / "check.py").write_text(content, encoding="utf-8")
        result = await clip(SAMPLE)
        manifest["clips"][name] = result
        print(f"{name}: {len(result['frames'])} frames over {result['duration']:.1f} s, marks {result['marks']}",
              flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (ROOT / "video" / "src" / "tui.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")   # what the video reads
    shutil.rmtree(SAMPLE, ignore_errors=True)
    if not any(SAMPLE.parent.iterdir()):
        SAMPLE.parent.rmdir()


if __name__ == "__main__":
    asyncio.run(main())
