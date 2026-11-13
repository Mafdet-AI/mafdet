"""Models in the cloud for the agent (cloud.py) and what the chat client does for them (model.py)."""
import argparse
import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mafdet.agent import cloud, settings
from mafdet.agent.model import ChatModel, ModelError, completions_url
from mafdet.core import config


class Cloud(BaseHTTPRequestHandler):
    """A provider: /models, and streamed chat completions that can refuse a parameter first."""
    chunks: list = []
    bodies: list = []
    paths: list = []
    refuse = ""                 # a parameter answered with a 400 while it is in the request
    status = 200

    def do_GET(self):
        type(self).paths.append(self.path)
        data = json.dumps({"data": [{"id": "models/gemini-x-pro"}, {"id": "qwen-y-max"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        type(self).paths.append(self.path)
        if self.status != 200 or (self.refuse and self.refuse in body):
            data = json.dumps({"error": {"message": f"Unknown name \"{self.refuse}\": Cannot find field."}}).encode()
            self.send_response(self.status if self.status != 200 else 400)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in self.chunks:
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    Cloud.bodies, Cloud.paths, Cloud.refuse, Cloud.status = [], [], "", 200
    Cloud.chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Cloud)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_completions_url_keeps_a_versioned_base():
    assert completions_url("http://127.0.0.1:8079") == "http://127.0.0.1:8079/v1/chat/completions"
    assert completions_url("http://h:11434/") == "http://h:11434/v1/chat/completions"
    for base in (cloud.PROVIDERS["qwen"].url, cloud.PROVIDERS["gemini"].url + "/", cloud.PROVIDERS["openrouter"].url):
        assert completions_url(base) == base.rstrip("/") + "/chat/completions"


def test_gemini_style_calls_without_index_and_their_signature_go_back(server):
    sig = {"google": {"thought_signature": "c2ln"}}
    Cloud.chunks = [
        {"choices": [{"delta": {"tool_calls": [{"id": "a", "type": "function", "extra_content": sig,
                                                "function": {"name": "read_file", "arguments": '{"path": "x"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"id": "b", "type": "function",
                                                "function": {"name": "list_dir", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    reply = ChatModel(server + "/v1beta/openai", "gemini-x", llama=False).chat([{"role": "user", "content": "hi"}])
    assert [(c["id"], c["name"]) for c in reply.tool_calls] == [("a", "read_file"), ("b", "list_dir")]
    calls = reply.message()["tool_calls"]
    assert calls[0]["extra_content"] == sig and "extra_content" not in calls[1]
    assert Cloud.paths == ["/v1beta/openai/chat/completions"]


def test_qwen_is_told_to_think_with_enable_thinking(server):
    model = ChatModel(server + "/compatible-mode/v1", "qwen-y-max", llama=False, thinking="enable_thinking")
    model.chat([{"role": "user", "content": "hi"}], think=True)
    model.chat([{"role": "user", "content": "hi"}], think=False)
    assert [b["enable_thinking"] for b in Cloud.bodies] == [True, False]
    assert all("reasoning_effort" not in b and "cache_prompt" not in b for b in Cloud.bodies)


def test_a_refused_optional_parameter_is_dropped_and_stays_off(server):
    Cloud.refuse = "reasoning_effort"
    model = ChatModel(server + "/v1", "m", llama=False)
    assert model.chat([{"role": "user", "content": "hi"}], think=False).content == "ok"
    assert model.chat([{"role": "user", "content": "hi"}], think=False).content == "ok"
    assert ["reasoning_effort" in b for b in Cloud.bodies] == [True, False, False]
    assert all("stream_options" in b for b in Cloud.bodies)          # only the one named goes


def test_other_errors_still_fail(server):
    Cloud.status = 401
    with pytest.raises(ModelError, match="401"):
        ChatModel(server + "/v1", "m", llama=False, api_key="bad").chat([{"role": "user", "content": "hi"}])


def args_for(**kw):
    base = dict(provider="gemini", model="", api_key="", ctx=0, url="", engine="llama", list_models=False)
    return argparse.Namespace(**{**base, **kw})


def test_resolve_fills_in_the_endpoint_from_the_environment(server, monkeypatch, capsys):
    monkeypatch.setitem(cloud.PROVIDERS, "gemini", dataclasses.replace(cloud.PROVIDERS["gemini"], url=server + "/v1"))
    monkeypatch.setenv("GEMINI_API_KEY", "k-env")
    args = args_for(model="gemini-x-pro")
    provider = cloud.resolve(args)
    assert (args.url, args.engine, args.api_key, args.ctx) == (server + "/v1", "openai", "k-env", provider.ctx)
    assert cloud.resolve(args) is provider and "go to the provider" in capsys.readouterr().err


def test_resolve_without_a_key_says_where_to_get_one(monkeypatch):
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        cloud.resolve(args_for(model="x"))


def test_list_models_and_a_missing_model(server, monkeypatch, capsys):
    monkeypatch.setitem(cloud.PROVIDERS, "qwen", dataclasses.replace(cloud.PROVIDERS["qwen"], url=server + "/v1"))
    with pytest.raises(SystemExit) as out:
        cloud.resolve(args_for(provider="qwen", api_key="k", list_models=True))
    assert out.value.code == 0 and capsys.readouterr().out.split() == ["gemini-x-pro", "qwen-y-max"]
    with pytest.raises(SystemExit) as out:
        cloud.resolve(args_for(provider="qwen", api_key="k"))
    assert out.value.code == 2 and "--model" in capsys.readouterr().err
    assert Cloud.paths == ["/v1/models", "/v1/models"]


def test_a_saved_model_belongs_to_its_provider():
    config.write_json("agent.json", {"provider": "gemini", "model": "gemini-x-pro"})
    a = argparse.Namespace(provider=None, model=None, approve=None, mcp=None, profile=None, tools=None, web=None)
    settings(a)
    assert (a.provider, a.model) == ("gemini", "gemini-x-pro")
    b = argparse.Namespace(provider="local", model=None, approve=None, mcp=None, profile=None, tools=None, web=None)
    settings(b)
    assert (b.provider, b.model) == ("", "")
    c = argparse.Namespace(provider=None, model=None, approve=None, mcp=None, profile=None, tools=None, web=None,
                           url="http://127.0.0.1:9000")
    settings(c)
    assert c.provider == ""
