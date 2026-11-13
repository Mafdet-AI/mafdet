"""`/model` and `/reasoning` in the agent: choosing what the agent runs on without leaving it.

The model picker goes in up to three steps. Where: the models on this machine, or a provider in the cloud
(each said with where its key is: an environment variable, the system's credential store, or none yet).
The key, when the provider has none: typed in hidden, checked by asking the provider for its models, and
kept in the credential store (keys.py). The model: the provider's own list (or the local ones), filtered as
you type. Esc goes back a step, and out from the first. The result is a switch.Choice, or None.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..identity import AMBER
from ..tui.fx import ALARM, DEEP, DIM, FAINT, HOT, LINE, SURFACE, TEXT
from . import cloud, keys
from .model import LEVELS
from .switch import Choice, local_models

LOCAL = "local"
CSS = f"""
{{name}} {{{{ align: center middle; background: #00000099; }}}}
{{name}} > Vertical {{{{ width: 84; max-width: 95%; height: auto; max-height: 85%; border: round {AMBER};
    background: {SURFACE}; padding: 0 1; }}}}
{{name}} #pick-title {{{{ color: {HOT}; text-style: bold; margin: 0 0 1 0; }}}}
{{name}} Input {{{{ border: round {LINE}; background: {SURFACE}; margin: 0 0 1 0; }}}}
{{name}} Input:focus {{{{ border: round {AMBER}; }}}}
{{name}} OptionList {{{{ height: auto; max-height: 20; border: none; background: {SURFACE}; padding: 0; }}}}
{{name}} OptionList > .option-list--option-highlighted {{{{ background: {DEEP}; color: {HOT}; text-style: none; }}}}
{{name}} #pick-hint {{{{ color: {FAINT}; margin: 1 0 0 0; }}}}
"""


def _where(provider: cloud.Provider) -> tuple[str, str]:
    """(the key's place as shown, the key)."""
    key, where = keys.get(provider.name, provider.keys)
    shown = {"store": "key saved", "session": "key for this session", "": "no key yet"}.get(where, f"key in {where}")
    return shown, key


class ModelPicker(ModalScreen):
    CSS = CSS.format(name="ModelPicker")
    BINDINGS = [Binding("escape", "back", show=False)]

    def __init__(self, current: Choice):
        super().__init__()
        self.current = current
        self.step = "where"
        self.provider: cloud.Provider | None = None
        self.models: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="pick-title")
            yield Input(id="pick-input")
            yield OptionList(id="pick-list")
            yield Static(id="pick-hint")

    def on_mount(self):
        self._show_where()

    # ------------------------------------------------------------ the steps

    def _set(self, title: str, hint: str, input_shown: bool, placeholder: str = "", password: bool = False):
        self.query_one("#pick-title", Static).update(title)
        self.query_one("#pick-hint", Static).update(hint)
        box = self.query_one("#pick-input", Input)
        box.display = input_shown
        box.password = password
        box.placeholder = placeholder
        box.value = ""
        options = self.query_one("#pick-list", OptionList)
        options.clear_options()
        (box if input_shown else options).focus()

    def _show_where(self):
        self.step, self.provider = "where", None
        self._set("Which model?", "Enter chooses · Esc closes", False)
        here = Text()
        here.append("This machine", style=f"bold {TEXT}")
        here.append("   the models downloaded here: nothing leaves it", style=DIM)
        options = [Option(self._mark(here, not self.current.provider), id=LOCAL)]
        for p in cloud.PROVIDERS.values():
            shown, key = _where(p)
            t = Text()
            t.append(p.title, style=f"bold {TEXT}")
            t.append(f"   {shown}", style=DIM if key else FAINT)
            options.append(Option(self._mark(t, self.current.provider == p.name), id=p.name))
        listed = self.query_one("#pick-list", OptionList)
        listed.add_options(options)
        listed.highlighted = next((i for i, o in enumerate(options) if o.id == (self.current.provider or LOCAL)), 0)

    @staticmethod
    def _mark(t: Text, current: bool) -> Text:
        return Text.assemble(("● " if current else "  ", AMBER), t)

    def _show_key(self, error: str = ""):
        p = self.provider
        self.step = "key"
        hint = Text()
        if error:
            hint.append(error + "\n", style=ALARM)
        hint.append(f"Get one at {p.signup}. It is kept in the system's credential store, not in a file; "
                    f"{p.keys[0]} in the environment always wins. Enter checks it · Esc goes back", style=FAINT)
        self._set(f"The API key for {p.title}", "", True, "paste the key", password=True)
        self.query_one("#pick-hint", Static).update(hint)

    def _show_models(self, models: list[tuple[str, str]], title: str, hint: str):
        self.step, self.models = "models", models
        self._set(title, hint, True, "type to filter")
        self._fill("")

    def _fill(self, query: str):
        options = self.query_one("#pick-list", OptionList)
        options.clear_options()
        words = query.lower().split()
        shown = [(i, d) for i, d in self.models if all(w in (i + " " + d).lower() for w in words)]
        items = []
        if self.provider is not None and not query:
            items.append(Option(Text("↻ use another API key", style=DIM), id="\0key"))
        for model_id, desc in shown[:300]:
            t = Text(model_id, style=TEXT)
            if desc:
                t.append(f"   {desc}", style=DIM)
            items.append(Option(self._mark(t, self.current == Choice(self.provider.name if self.provider else "",
                                                                       model_id)), id=model_id))
        if not shown:
            items.append(Option(Text("  nothing matches", style=FAINT), id="\0none", disabled=True))
        options.add_options(items)
        if items:                       # on the model in use when it is listed, else on the first model
            here = self.current.provider == (self.provider.name if self.provider else "")
            options.highlighted = next((i for i, o in enumerate(items) if here and o.id == self.current.model),
                                       1 if items[0].id == "\0key" and len(items) > 1 else 0)

    def _local(self):
        models = local_models()
        if not models:
            self._set("No model on this machine yet", "Download one in the app (mafdet) or with "
                      "`mafdet models --download qwen3-14b`. Esc goes back", False)
            self.step = "models"
            return
        self.provider = None
        self._show_models(models, "A model on this machine",
                          "Enter starts it in llama-server (it takes a few seconds) · Esc goes back")

    def _ask_provider(self, key: str, checking: bool):
        """The provider's models, off the interface's thread: they also tell whether the key works."""
        p = self.provider
        self.query_one("#pick-hint", Static).update(Text(f"asking {p.title} for its models…", style=DIM))

        def work():
            try:
                models = cloud.list_models(p, key)
                error = ""
            except SystemExit as e:
                models, error = [], str(e)
            self.app.call_from_thread(self._listed, p, key, models, error, checking)
        self.run_worker(work, thread=True)

    def _listed(self, p, key: str, models: list[str], error: str, checking: bool):
        if self.provider is not p:
            return                                  # gone back meanwhile
        if error:
            if checking:
                return self._show_key("That key did not work: " + error.split(": ", 1)[-1][:200])
            return self._show_key("The saved key did not work: " + error.split(": ", 1)[-1][:200])
        if checking:
            where = keys.put(p.name, key)
            note = "kept in the credential store" if where == "store" else "kept for this session only"
        else:
            note = ""
        self._show_models([(m, "") for m in models], f"A model of {p.title}",
                          (f"Key {note}. " if note else "") + "Enter uses it · type to filter · Esc goes back")

    # ------------------------------------------------------------ events

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        chosen = event.option.id
        if self.step == "where":
            if chosen == LOCAL:
                return self._local()
            self.provider = cloud.PROVIDERS[chosen]
            _, key = _where(self.provider)
            if not key:
                return self._show_key()
            self._show_models([], f"A model of {self.provider.title}", "")
            return self._ask_provider(key, checking=False)
        if self.step == "models":
            if chosen == "\0key":
                return self._show_key()
            if chosen and not chosen.startswith("\0"):
                self.dismiss(Choice(self.provider.name if self.provider else "", chosen))

    def on_input_changed(self, event: Input.Changed):
        if self.step == "models":
            self._fill(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        if self.step == "key":
            key = event.value.strip()
            if key:
                self._ask_provider(key, checking=True)
            return
        if self.step == "models":
            options = self.query_one("#pick-list", OptionList)
            if options.highlighted is not None:
                options.action_select()

    def on_key(self, event):
        if self.step == "models" and event.key in ("up", "down", "pageup", "pagedown"):
            event.prevent_default()
            event.stop()
            options = self.query_one("#pick-list", OptionList)
            getattr(options, {"up": "action_cursor_up", "down": "action_cursor_down",
                              "pageup": "action_page_up", "pagedown": "action_page_down"}[event.key])()

    def action_back(self):
        if self.step == "where":
            self.dismiss(None)
        else:
            self._show_where()


LEVEL_TEXT = {"auto": "the model's or the provider's own default",
              "off": "no reasoning: the fastest, for simple steps",
              "low": "a little",
              "medium": "a fair amount",
              "high": "as much as it takes: the slowest and, in the cloud, the most expensive"}


class ReasoningPicker(ModalScreen):
    CSS = CSS.format(name="ReasoningPicker")
    BINDINGS = [Binding("escape", "back", show=False)]

    def __init__(self, current: str, local: bool):
        super().__init__()
        self.current, self.local = current, local

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("How much should the model reason?", id="pick-title")
            yield OptionList(id="pick-list")
            yield Static(id="pick-hint")

    def on_mount(self):
        options = self.query_one("#pick-list", OptionList)
        for level in LEVELS:
            t = Text(f"{level:<8}", style=f"bold {TEXT}")
            t.append(LEVEL_TEXT[level], style=DIM)
            options.add_option(Option(ModelPicker._mark(t, level == self.current), id=level))
        options.highlighted = LEVELS.index(self.current) if self.current in LEVELS else 0
        options.focus()
        self.query_one("#pick-hint", Static).update(Text(
            ("A local model only reasons or not: low, medium and high all mean on. " if self.local else "")
            + "Enter chooses · Esc closes", style=FAINT))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        self.dismiss(event.option.id)

    def action_back(self):
        self.dismiss(None)
