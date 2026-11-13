"""What the agent runs on, and changing it during a session (`/model` and `/reasoning` in the interface).

One Runtime per `mafdet agent`: the model the agent talks to (a ChatModel), the llama-server it started
when the model is local, the provider when it is in the cloud, the context, the reasoning level, and the
thinking ahead that goes with a local reasoning model. Switching to another model keeps the conversation:
the new model goes on from the same history. Leaving a local model stops its llama-server; going back to
one starts it again (the context chosen again for that model, as at a start). The choice is remembered in
~/.mafdet/agent.json for the next start, as --save would.
"""
from __future__ import annotations

import argparse
import threading
from dataclasses import dataclass

from .model import LEVELS, ChatModel


@dataclass(frozen=True)
class Choice:
    provider: str               # "" for a local model
    model: str                  # a catalog id or a GGUF for a local one, the provider's id for the cloud


def local_models() -> list[tuple[str, str]]:
    """(id, a short description) of the models on this machine: the catalog's downloaded ones, then any
    other GGUF of ~/.mafdet/models or added in the app."""
    from ..core import catalog
    out, seen = [], set()
    for v in catalog.variants() + catalog.local_variants():
        if v.id in seen:
            continue
        seen.add(v.id)
        try:
            path = catalog.local_path(v)
        except Exception:
            path = None
        if path:
            gb = v.bytes / 1e9 if v.bytes else 0
            out.append((v.id, f"{v.model.get('name', v.model.get('id', ''))} {v.quant}"
                              + (f" · {gb:.1f} GB" if gb else "")))
    return out


class Runtime:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.engine = None              # the llama-server started for a local model
        self.provider = None            # cloud.Provider, for a model in the cloud
        self.model: ChatModel | None = None
        self.label = ""
        self.ctx = 0
        self.ahead = None               # ahead.Ahead, with a local reasoning model in the interface
        self.wants_ahead = False
        self.effort = getattr(args, "reasoning", "auto") or "auto"
        if not getattr(args, "think", True):
            self.effort = "off"
        self.lock = threading.Lock()

    # ------------------------------------------------------------ what the agent sees

    @property
    def think(self) -> bool:
        return self.effort != "off"

    @property
    def agent_model(self):
        """The model as the agent gets it: wrapped with thinking ahead when there is one."""
        if self.ahead is not None:
            from .ahead import AheadModel
            return AheadModel(self.model, self.ahead)
        return self.model

    @property
    def choice(self) -> Choice:
        return Choice(self.provider.name if self.provider else "", self.args.model or "")

    @property
    def local(self) -> bool:
        return self.provider is None and self.args.engine == "llama"

    # ------------------------------------------------------------ starting and switching

    def start(self, ahead: bool = False):
        """The model of the command line (or of agent.json). `ahead`: the interface wants thinking ahead."""
        from . import remote_model, start_engine
        self.wants_ahead = ahead
        model, label = remote_model(self.args)
        self.provider = getattr(self.args, "cloud", None)
        if model is None:
            self.engine, alias = start_engine(self.args)
            model, label = ChatModel(self.engine.url, alias), alias
        self._use(model, label)

    def switch(self, choice: Choice, key: str = "", progress=None):
        """Run on `choice` from now on. Slow for a local model (llama-server loads it): call it off the
        interface's thread. `progress(text)` hears what is happening. Raises SystemExit with what is wrong
        (no key, a model not downloaded, an engine that does not start); the model in use stays, or, when its
        llama-server had to stop first, is started again."""
        from . import cloud, remote_model, start_engine
        say = progress or (lambda text: None)
        with self.lock:
            args = argparse.Namespace(**vars(self.args))
            for name in ("cloud", "thinking"):
                if hasattr(args, name):
                    delattr(args, name)
            args.model, args.provider, args.api_key, args.ctx = choice.model, choice.provider, key, 0
            args.url, args.engine, args.list_models = "", "llama", False
            if choice.provider:
                cloud.resolve(args)
                model, label = remote_model(args)
            else:
                from ..proxy.launch import resolve_model
                resolve_model(choice.model)             # not downloaded: said before anything stops
            before = (self.args, self.engine is not None)
            self._close_ahead()
            if self.engine is not None:
                say("stopping llama-server")
                self.engine.close()
                self.engine = None
            if not choice.provider:
                say(f"starting llama-server with {choice.model}: loading the model")
                try:
                    self.engine, alias = start_engine(args)
                except BaseException as e:
                    self._restore(*before, say)
                    raise SystemExit(f"llama-server did not start with {choice.model}: {e}") from None
                model, label = ChatModel(self.engine.url, alias), alias
            self.args = args
            self.provider = getattr(args, "cloud", None)
            self._use(model, label)
            try:
                remember(args)
            except Exception:
                pass

    def _restore(self, args, had_engine: bool, say):
        """Back to the model in use before a switch that failed."""
        from . import start_engine
        if had_engine:
            say("starting the previous model again")
            self.engine, alias = start_engine(args)
            self._use(ChatModel(self.engine.url, alias), alias)
        elif self.model is not None:
            self._use(self.model, self.label)

    def set_effort(self, effort: str):
        if effort not in LEVELS:
            raise ValueError(effort)
        self.effort = effort
        if self.model is not None:
            self.model.effort = effort
        if self.ahead is not None:
            self.ahead.on = self.think
        try:
            from ..core import config
            config.write_json("agent.json", {**config.read_json("agent.json"), "reasoning": effort})
        except Exception:
            pass

    def _use(self, model: ChatModel, label: str):
        model.effort = self.effort
        self.model, self.label, self.ctx = model, label, self.args.ctx
        if self.wants_ahead and self.think and self.local:
            from .ahead import Ahead
            self.ahead = Ahead.for_engine(model.url, self.ctx)

    def _close_ahead(self):
        if self.ahead is not None:
            self.ahead.close()
            self.ahead = None

    def close(self):
        self._close_ahead()
        if self.engine is not None:
            self.engine.close()
            self.engine = None


def remember(args):
    """The provider, the model and the reasoning level in agent.json, for the next start."""
    from ..core import config
    saved = config.read_json("agent.json")
    saved.update({"provider": args.provider or "", "model": args.model or ""})
    config.write_json("agent.json", saved)
