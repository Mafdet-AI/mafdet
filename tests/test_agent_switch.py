"""Changing the agent's model during a session: keys, reasoning levels, the Runtime, /model and /reasoning."""
import argparse
import asyncio
import dataclasses
import threading

import pytest

from mafdet.agent import cloud, keys, switch
from mafdet.agent.model import ChatModel, reasoning_params
from mafdet.core import config
from test_agent import Streams, tui_on, until
from test_agent_cloud import Cloud, server  # noqa: F401  (the fixture)


def test_reasoning_params_per_kind_of_engine():
    assert reasoning_params("reasoning_effort", True) == {}
    assert reasoning_params("reasoning_effort", True, "high") == {"reasoning_effort": "high"}
    assert reasoning_params("reasoning_effort", False, "high") == {"reasoning_effort": "none"}
    assert reasoning_params("enable_thinking", True, "low") == {"enable_thinking": True, "thinking_budget": 2048}
    assert reasoning_params("enable_thinking", True) == {"enable_thinking": True}
    assert reasoning_params("enable_thinking", False, "medium") == {"enable_thinking": False}
    assert reasoning_params("reasoning", True, "medium") == {"reasoning": {"effort": "medium"}}
    assert reasoning_params("reasoning", False) == {"reasoning": {"enabled": False}}


def test_keys_environment_first_then_the_store(keyring_store, monkeypatch):
    assert keys.get("gemini", ("GEMINI_API_KEY",)) == ("", "")
    assert keys.put("gemini", " k-typed ") == "store"
    assert keyring_store.saved == {("mafdet", "gemini"): "k-typed"}
    assert keys.get("gemini", ("GEMINI_API_KEY",)) == ("k-typed", "store")
    monkeypatch.setenv("GEMINI_API_KEY", "k-env")
    assert keys.get("gemini", ("GEMINI_API_KEY",)) == ("k-env", "GEMINI_API_KEY")
    keys.forget("gemini")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert keys.get("gemini", ("GEMINI_API_KEY",)) == ("", "")


def test_keys_without_a_store_live_for_the_session(monkeypatch):
    monkeypatch.setattr(keys, "_keyring", lambda: None)
    assert keys.put("qwen", "k") == "session" and keys.get("qwen") == ("k", "session")


@pytest.fixture
def providers(server, monkeypatch):  # noqa: F811
    for name in cloud.PROVIDERS:                    # all of them: no request leaves the machine
        monkeypatch.setitem(cloud.PROVIDERS, name, dataclasses.replace(cloud.PROVIDERS[name], url=server + "/v1"))
    return server


def namespace(**kw):
    base = dict(provider="", model="", api_key="", ctx=0, url="", engine="llama", list_models=False,
                reasoning="auto", think=True, approve="ask", device="auto", engine_port=8079, llama_args="")
    return argparse.Namespace(**{**base, **kw})


class FakeServer:
    """llama-server as start_engine gives it back."""
    started: list = []

    def __init__(self, model, fail=False):
        if fail:
            raise RuntimeError("out of memory")
        self.url, self.closed = "http://127.0.0.1:1", False
        FakeServer.started.append(model)

    def close(self):
        self.closed = True


@pytest.fixture
def local_engine(monkeypatch):
    import mafdet.agent as agent_pkg
    import mafdet.proxy.launch as launch
    FakeServer.started = []
    failing = set()

    def start(args):
        args.ctx = args.ctx or 32768
        return FakeServer(args.model, fail=args.model in failing), args.model
    monkeypatch.setattr(agent_pkg, "start_engine", start)
    monkeypatch.setattr(launch, "resolve_model", lambda spec: ("x.gguf", spec, ()))
    return failing


def test_runtime_switches_between_the_cloud_and_this_machine(providers, local_engine, keyring_store):
    keys.put("gemini", "k-g")
    keys.put("qwen", "k-q")
    rt = switch.Runtime(namespace(provider="gemini", model="gemini-x-pro", reasoning="high"))
    rt.start()
    assert rt.provider.name == "gemini" and rt.model.effort == "high" and rt.ctx == cloud.PROVIDERS["gemini"].ctx
    assert rt.label == "gemini-x-pro · Google Gemini" and rt.engine is None

    said = []
    rt.switch(switch.Choice("", "qwen3-14b"), progress=said.append)
    assert rt.local and rt.engine is not None and rt.label == "qwen3-14b" and rt.ctx == 32768
    assert any("starting llama-server" in s for s in said)
    first = rt.engine

    rt.switch(switch.Choice("qwen", "qwen-y-max"))
    assert first.closed and rt.engine is None and rt.model.thinking == "enable_thinking"
    assert rt.model.api_key == "k-q" and rt.choice == switch.Choice("qwen", "qwen-y-max")
    saved = config.read_json("agent.json")
    assert (saved["provider"], saved["model"]) == ("qwen", "qwen-y-max")

    rt.set_effort("off")
    assert not rt.think and rt.model.effort == "off" and config.read_json("agent.json")["reasoning"] == "off"


def test_a_failed_switch_keeps_or_restarts_the_model_in_use(providers, local_engine):
    rt = switch.Runtime(namespace(model="qwen3-4b"))
    rt.start()
    local_engine.add("qwen3-32b")
    with pytest.raises(SystemExit, match="out of memory"):
        rt.switch(switch.Choice("", "qwen3-32b"))
    assert FakeServer.started == ["qwen3-4b", "qwen3-4b"] and rt.label == "qwen3-4b" and rt.engine is not None

    with pytest.raises(SystemExit, match="API key"):              # no key for the provider: nothing stops
        rt.switch(switch.Choice("gemini", "gemini-x-pro"))
    assert not rt.engine.closed and rt.label == "qwen3-4b"


# ---------------------------------------------------------------- in the interface

def runtime_on(url, model):
    rt = switch.Runtime(namespace(provider="gemini", model="gemini-x-pro"))
    rt.model, rt.label, rt.ctx = model, "fake-model", 16384
    return rt


def test_model_picker_takes_a_key_lists_the_models_and_switches(providers, tmp_path, keyring_store):
    async def go():
        model = Streams([])
        app = tui_on(tmp_path, model)
        rt = runtime_on(providers, model)
        app.runtime = rt
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"/model", "enter")
            await pilot.pause(0.2)
            from mafdet.agent.picker import ModelPicker
            assert isinstance(app.screen, ModelPicker)
            await pilot.press("home", *["down"] * (1 + list(cloud.PROVIDERS).index("gemini")), "enter")   # no key yet
            await pilot.pause(0.2)
            assert app.screen.step == "key"
            await pilot.press(*"k-new", "enter")                                    # checked by listing
            await until(pilot, lambda: app.screen.step == "models" and app.screen.models)
            assert keyring_store.saved[("mafdet", "gemini")] == "k-new"
            await pilot.press(*"qwen-y")                                            # the filter
            await pilot.pause(0.1)
            await pilot.press("enter")
            await until(pilot, lambda: not app.running and app.label != "fake-model")
            assert app.label == "qwen-y-max · Google Gemini"
            assert app.agent.model.model == "qwen-y-max" and app.agent.model.api_key == "k-new"
            assert config.read_json("agent.json")["model"] == "qwen-y-max"
    asyncio.run(go())


def test_a_wrong_key_is_said_and_asked_again(providers, tmp_path, keyring_store):
    Cloud.refuse = ""

    async def go():
        model = Streams([])
        app = tui_on(tmp_path, model)
        app.runtime = runtime_on(providers, model)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"/model", "enter")
            await pilot.pause(0.2)
            await pilot.press("home", *["down"] * (1 + list(cloud.PROVIDERS).index("qwen")), "enter")
            await pilot.pause(0.2)
            Cloud.status = 401
            await pilot.press(*"bad", "enter")
            await until(pilot, lambda: "did not work" in str(app.screen.query_one("#pick-hint").render()))
            assert app.screen.step == "key" and ("mafdet", "qwen") not in keyring_store.saved
            await pilot.press("escape", "escape")
            await pilot.pause(0.1)
            assert app.screen is app.screen_stack[0] and app.label == "fake-model"
    asyncio.run(go())


def test_reasoning_command_sets_the_level(tmp_path):
    async def go():
        model = Streams([])
        app = tui_on(tmp_path, model)
        rt = switch.Runtime(namespace())
        rt.model, rt.label, rt.ctx = ChatModel("http://127.0.0.1:1", "m", llama=False), "m", 16384
        rt.provider = cloud.PROVIDERS["gemini"]
        app.runtime = rt
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"/reasoning high", "enter")
            await pilot.pause(0.1)
            assert rt.effort == "high" and rt.model.effort == "high" and app.agent.think
            assert "reasoning high" in str(app.query_one("#status").render())
            await pilot.press(*"/reasoning", "enter")                                # completed: the levels show
            await pilot.pause(0.2)
            assert app.query_one("#prompt").text == "/reasoning "
            await pilot.press("down", "enter")                                       # auto, off
            await pilot.pause(0.1)
            assert rt.effort == "off" and not app.agent.think
    asyncio.run(go())


def test_runtime_lock_is_released_after_a_failure(providers, local_engine):
    rt = switch.Runtime(namespace(model="qwen3-4b"))
    rt.start()
    local_engine.add("bad")
    with pytest.raises(SystemExit):
        rt.switch(switch.Choice("", "bad"))
    assert rt.lock.acquire(timeout=1)
    rt.lock.release()
    assert isinstance(rt.lock, type(threading.Lock()))
