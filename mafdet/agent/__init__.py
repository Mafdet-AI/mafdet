"""`mafdet agent`: a local model that works with tools, on this machine.

The model reads and writes files, runs commands and uses the tools of MCP servers (a decompiler, a
debugger) in a loop, until the task is done; the harness keeps the prompt inside a small context and
asks before anything that changes the machine. Built first for reverse engineering and security
study, where the binary should not leave the machine.

    tui.py          the conversation in the terminal: what `mafdet agent` opens
    loop.py         the loop, and what a local model gets wrong told back to it
    model.py        one streamed chat completion with tools (llama-server, or any OpenAI-compatible engine)
    tools.py        read_file, write_file, edit_file, search, shell
    mcp.py          MCP servers as tools; profiles and find_tools, so two hundred tools fit
    context.py      the budget: outputs cut to size, old ones left out
    permissions.py  what runs by itself and what asks
    store.py        the session on disk
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

ENGINE_PORT = 8079

SYSTEM = """You are MAFDET, an agent working on the user's own machine through tools. \
You help with reverse engineering, security research and programming.

Working folder: {workdir}
System: {system}

How to work:
- Act through the tools; do not describe what you would do, do it. One step at a time: look at each result before the next call.
- Say only what you have seen in a tool result. If you have not checked something, check it or say it is a guess.
- Tool outputs are cut when long, and old ones are left out of the conversation: when you find something that matters (an address, a name, a value), keep it with `note`, and ask for narrow outputs (one function, one range, a pattern) instead of whole listings.
- For a task of several steps, set the steps with `todo` first and update it as you go.
- The shell stays open between commands. For something that must keep running (a server, a debugger), use `background` and read it with `job`.
- The user approves commands and writes: if a call is declined, do not repeat it.
- When the task is done, answer briefly: what you found, where, and what is left open."""


def system_prompt(workdir: Path, extra: str = "") -> str:
    text = SYSTEM.format(workdir=workdir, system=f"{platform.system()} {platform.release()}")
    return text + ("\n\n" + extra.strip() if extra.strip() else "")


def _paint(text: str, style: str) -> str:
    from ..cli import paint
    return paint(text, style)


class Printer:
    """The loop's events on the terminal: reasoning dim, tools in amber, the answer plain."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.kind = ""

    def _w(self, text: str):
        self.out.write(text)
        self.out.flush()

    def __call__(self, kind: str, **d):
        from ..cli import AMBER, DIM
        if kind in ("think", "text"):
            if self.kind != kind:
                self._w("\n" if self.kind else "")
            self._w(_paint(d["text"], DIM) if kind == "think" else d["text"])
            self.kind = kind
            return
        if self.kind in ("think", "text"):
            self._w("\n")
        self.kind = kind
        if kind == "tool":
            args = d["args"] if isinstance(d["args"], str) else json.dumps(d["args"], ensure_ascii=False)
            self._w(_paint(f"> {d['name']} ", AMBER) + (args if len(args) <= 300 else args[:300] + "…") + "\n")
        elif kind == "result":
            lines = d["text"].splitlines()
            shown = "\n".join("  " + line[:160] for line in lines[:8])
            more = f"\n  … {len(lines) - 8} more lines" if len(lines) > 8 else ""
            self._w(_paint(f"{shown}{more}\n  ({d['chars']} characters, {d['seconds']:.1f} s)", DIM) + "\n")
        elif kind == "declined":
            self._w(_paint(f"  declined: {d['name']}", DIM) + "\n")
        elif kind == "left_out":
            self._w(_paint(f"  context: {d['n']} old tool outputs left out", DIM) + "\n")
        elif kind == "compacted":
            self._w(_paint(f"  context: the conversation so far was summarized, about {d['before']} -> "
                           f"{d['after']} tokens", DIM) + "\n")
        elif kind == "stopped":
            self._w(_paint("  stopped", DIM) + "\n")
        elif kind == "step":
            total = d["prompt_tokens"] + d["completion_tokens"]
            speed = d["completion_tokens"] / d["seconds"] if d["seconds"] else 0
            self._w(_paint(f"  [step {d['n']}: {total}/{d['limit']} tokens, {d['seconds']:.1f} s, "
                           f"{speed:.0f} tok/s]", DIM) + "\n")
        elif kind == "error":
            self._w(_paint(f"! {d['text']}", AMBER) + "\n")


def ask_in_terminal(tool, args: dict, why: str) -> str:
    from ..cli import AMBER
    shown = json.dumps(args, ensure_ascii=False, indent=1)
    print(_paint(f"? {tool.name}", AMBER) + f" ({why})\n{shown}")
    try:
        return input("  run it? [y]es / [a]lways for this tool / [N]o: ")
    except EOFError:
        return "n"


SAVED = {"model": "", "approve": "ask", "mcp": [], "profile": "", "tools": "", "web": False}


def settings(args) -> dict:
    """The options that stay from one start to the next (~/.mafdet/agent.json): what the command line
    does not say comes from there. --save writes the ones in use. `search_url` (a SearXNG) is only in the file."""
    from ..core import config
    saved = config.read_json("agent.json")
    for key, default in SAVED.items():
        if getattr(args, key, None) is None:
            value = saved.get(key, default)
            setattr(args, key, value if type(value) is type(default) else default)
    args.search_url = str(saved.get("search_url") or "")
    if getattr(args, "save", False):
        config.write_json("agent.json", {**saved, **{key: getattr(args, key) for key in SAVED}})
    return saved


def build_toolbox(args, workdir: Path, stop=None, memory=None, shell=None):
    """The agent's own tools, plus the MCP servers named with --mcp. Returns (toolbox, servers, their
    instructions)."""
    from . import mcp
    from .store import sessions_dir
    from .tools import builtin_tools
    # Reads of the saved outputs of any session count as inside: /new changes session, not tools.
    tools = builtin_tools(workdir, read_roots=(sessions_dir(),), stop=stop, shell=shell)
    if memory is not None:
        from .memory import memory_tools
        tools += memory_tools(memory)
    if getattr(args, "web", False):
        from .web import web_tools
        tools += web_tools(getattr(args, "search_url", ""))
    box = mcp.Toolbox(tools)
    servers, notes = [], []
    known = mcp.configured()
    patterns = [p.strip() for p in (args.tools or "").split(",") if p.strip()]
    if args.profile:
        if args.profile not in mcp.PROFILES:
            raise SystemExit(f"No profile {args.profile}: {', '.join(mcp.PROFILES)}.")
        patterns += mcp.PROFILES[args.profile]
    for name in args.mcp or []:
        conf = known.get(name)
        if not isinstance(conf, dict) or not conf.get("command"):
            from ..core import config
            raise SystemExit(f"No MCP server named {name} in {config.home() / 'mcp.json'}"
                             + (f" (there: {', '.join(known)})." if known else
                                ': write it as {"mcpServers": {"%s": {"command": "...", "args": [...]}}}.' % name))
        print(f"MCP server {name}: starting...", file=sys.stderr, flush=True)
        server = mcp.McpServer(name, conf["command"], conf.get("args") or [], conf.get("env"), conf.get("cwd"),
                               log=mcp.log_path(sessions_dir(), name))
        servers.append(server)
        own = [str(p) for p in conf.get("tools") or []]
        box.add_server(server, (patterns + own) or None, [str(p) for p in conf.get("allow") or []])
        if server.instructions:
            notes.append(f"About the tools of {name}: {mcp._short(server.instructions, 600)}")
    return box, servers, notes


CONTEXTS = (32768, 24576, 16384)       # what --ctx 0 chooses among; the last one is the floor


def pick_ctx(path: str, hw=None, device: str = "cuda") -> int:
    """The largest context that leaves the model all on the GPU here, by the catalog's estimate: an
    agent's prompt only grows, and a context that pushes layers to the CPU costs more than it gives
    (Qwen3-14B Q4_K_M on 16 GB: 24k all on the GPU, 32k not). 16k when the model is split anyway, on
    the CPU, and in unified memory, where the estimate has nothing to say."""
    from ..core import catalog, hardware
    try:
        hw = hw or hardware.detect(refresh=True)
        if device != "cuda" or not hw.gpu or hw.gpu.unified:
            return CONTEXTS[-1]
        meta = (catalog.header(Path(path)) or {}).get("meta") or {}
        trained = meta.get(f"{meta.get('general.architecture', '')}.context_length") or CONTEXTS[0]
        for ctx in CONTEXTS:
            if ctx <= trained and not catalog.splits_with_cpu(path, hw, ctx):
                return ctx
    except Exception as e:                      # never a reason not to start
        print(f"context left at {CONTEXTS[-1]}: {e}", file=sys.stderr)
    return CONTEXTS[-1]


def engine_ctx(url: str) -> int:
    """The context of an engine already running: llama-server says it, the others get 16k."""
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=5) as r:
            return int(json.loads(r.read())["default_generation_settings"]["n_ctx"])
    except Exception:
        return CONTEXTS[-1]


def start_engine(args):
    """llama-server for the agent, through the app's LlamaServer: one slot with the whole context."""
    from ..llama_backend import LlamaServer, pick_device
    from ..proxy.launch import resolve_model
    path, alias, model_args = resolve_model(args.model)
    device = pick_device(args.device)
    if not args.ctx:
        args.ctx = pick_ctx(path, device=device)
    extra = model_args + ("--alias", alias) + tuple(args.llama_args.split())
    print(f"Starting llama-server: {alias} on the {'GPU' if device == 'cuda' else 'CPU'}, {args.ctx} tokens of "
          f"context, port {args.engine_port}", file=sys.stderr, flush=True)
    return LlamaServer(path, device, ctx=args.ctx, n_slots=1, port=args.engine_port, extra_args=extra,
                       log_name="agent-server"), alias


PROJECT_FILES = ("MAFDET.md", "AGENTS.md")     # instructions of the folder, added to the system prompt
PROJECT_CHARS = 6000


def project_notes(workdir: Path) -> str:
    for name in PROJECT_FILES:
        try:
            text = (workdir / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return f"Instructions of this folder ({name}):\n{text[:PROJECT_CHARS]}"
    return ""


def run(args) -> int:
    """`mafdet agent`."""
    import threading

    from .loop import Agent
    from .model import ChatModel
    from .permissions import Permissions
    from .store import Session

    from .memory import Memory
    from .shell import Shell
    from .store import sessions_dir

    settings(args)
    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        raise SystemExit(f"{workdir} is not a folder.")
    session = Session.find(args.resume) if args.resume else Session()
    history = session.messages() if args.resume else None
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    stop = threading.Event()
    memory = Memory()
    shell = Shell(workdir, sessions_dir() / f"shell-{os.getpid()}", stop)
    box, servers, notes = build_toolbox(args, workdir, stop, memory, shell)
    notes.insert(0, project_notes(workdir))
    if args.system:
        notes.insert(0, Path(args.system).read_text(encoding="utf-8"))
    system = system_prompt(workdir, "\n\n".join(n for n in notes if n))
    engine = None
    try:
        if args.url:
            args.ctx = args.ctx or engine_ctx(args.url)
            model = ChatModel(args.url, args.model or "default", llama=args.engine == "llama", api_key=args.api_key)
            label = f"{model.model} at {args.url}"
        else:
            engine, alias = start_engine(args)
            model = ChatModel(engine.url, alias)
            label = alias

        def make_agent(emit, ask, mode=args.approve, fresh=False):
            return Agent(model, box, Permissions(mode, ask), Session() if fresh else session, system=system,
                         ctx=args.ctx, max_tokens=args.max_tokens, max_steps=args.max_steps,
                         tool_chars=args.tool_chars, temperature=args.temperature, think=args.think, emit=emit,
                         history=None if fresh else history, stop=stop, memory=memory)

        if interactive and not args.prompt and not args.plain:
            from .tui import AgentTUI
            AgentTUI(make_agent, label=label, workdir=str(workdir), mode=args.approve).run()
            return 0
        agent = make_agent(Printer(), ask_in_terminal if interactive else None)
        spec_tokens = len(json.dumps(box.specs())) // 3
        print(f"MAFDET agent · {label} · {workdir}\n"
              f"  tools     {len(box.active)} (about {spec_tokens} tokens)"
              + (f", {len(box.hidden)} more through find_tools" if box.hidden else "") + "\n"
              f"  context   {args.ctx} tokens\n"
              f"  approval  {args.approve}" + ("" if interactive or args.approve == "all" else
                                               " (no terminal to ask in: what would ask is declined)") + "\n"
              f"  session   {session.id}" + (f" (resumed, {len(history)} messages)" if history else ""),
              file=sys.stderr, flush=True)
        if args.prompt:
            return 0 if _turn(agent, args.prompt) else 1
        if not interactive:
            text = sys.stdin.read().strip()
            return (0 if _turn(agent, text) else 1) if text else 0
        print("  /tools lists the tools, /new starts over, /quit leaves; Ctrl+C stops the model.\n", file=sys.stderr)
        while True:
            try:
                text = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if text in ("/quit", "/exit"):
                return 0
            if text == "/tools":
                for t in box.active.values():
                    print(f"  {t.name:<24} {t.risk:<5} {t.source:<8} {t.description[:80]}")
                continue
            if text == "/new":
                agent = make_agent(Printer(), ask_in_terminal, fresh=True)
                print(f"  session   {agent.session.id}", file=sys.stderr)
                continue
            if text:
                _turn(agent, text)
    finally:
        stop.set()
        shell.close()
        for server in servers:
            server.close()
        if engine is not None:
            engine.close()


def _turn(agent, text: str) -> bool:
    try:
        answer = agent.run(text)
    except KeyboardInterrupt:
        print("\n  stopped", file=sys.stderr)
        return False
    print()
    return bool(answer)
