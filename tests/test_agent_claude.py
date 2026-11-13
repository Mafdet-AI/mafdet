"""Claude in the agent (claude.py): the conversation turned into a Messages request, and a streamed
answer read back, through Anthropic's own SDK against a fake Messages endpoint."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mafdet.agent import claude
from mafdet.agent.model import ModelError

THINK_TOOL = [  # thinking, a line of text, a tool call; Anthropic's SSE events
    {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                          "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                                          "stop_sequence": None,
                                          "usage": {"input_tokens": 100, "output_tokens": 1,
                                                    "cache_read_input_tokens": 900,
                                                    "cache_creation_input_tokens": 20}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Read a.txt first."}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-1"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Reading it."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.txt"}'}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
]


class Messages(BaseHTTPRequestHandler):
    events: list = []
    bodies: list = []
    headers_seen: list = []
    refuse: list = []          # parameters answered with a 400, one request each, in order

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        type(self).headers_seen.append(dict(self.headers))
        if self.refuse and self.refuse[0] in json.dumps(body):
            name = type(self).refuse.pop(0)
            data = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                          "message": f"{name}: Extra inputs are not permitted"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in self.events:
            self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def endpoint(monkeypatch):
    Messages.events, Messages.bodies, Messages.headers_seen, Messages.refuse = THINK_TOOL, [], [], []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Messages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    yield
    srv.shutdown()
    srv.server_close()


TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read a file.",
                                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                                                           "required": ["path"]}}}]


def test_a_streamed_turn_with_thinking_and_a_tool_call(endpoint):
    seen = []
    model = claude.ClaudeModel("claude-opus-5-5", api_key="k", effort="high")
    reply = model.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "what is in a.txt?"}],
                       TOOLS, max_tokens=4096, temperature=0.6, on_delta=lambda kind, text: seen.append((kind, text)))
    assert seen == [("think", "Read a.txt first."), ("text", "Reading it.")]
    assert reply.content == "Reading it." and reply.reasoning == "Read a.txt first." and reply.finish == "tool_calls"
    assert reply.tool_calls == [{"id": "toolu_1", "name": "read_file", "arguments": '{"path": "a.txt"}'}]
    assert (reply.prompt_tokens, reply.completion_tokens) == (1020, 42)
    assert reply.message()["claude"][0] == {"type": "thinking", "thinking": "Read a.txt first.", "signature": "sig-1"}

    body, headers = Messages.bodies[0], Messages.headers_seen[0]
    assert body["system"] == "sys" and "temperature" not in body and body["max_tokens"] == claude.MIN_TOKENS
    assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert body["output_config"] == {"effort": "high"} and body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert body["tools"][0]["input_schema"]["required"] == ["path"] and body["tools"][0]["eager_input_streaming"]
    headers = {k.lower(): v for k, v in headers.items()}
    betas = headers.get("anthropic-beta", "")
    assert claude.BINDING_BETA in betas and claude.FALLBACK_BETA in betas
    assert headers.get("x-api-key") == "k" or headers.get("authorization") == "Bearer k"     # as the SDK sends it


def test_thinking_goes_back_unchanged_and_tool_results_in_one_message(endpoint):
    model = claude.ClaudeModel("claude-sonnet-4-6", api_key="k")
    first = model.chat([{"role": "user", "content": "go"}], TOOLS)
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}, first.message(),
               {"role": "tool", "tool_call_id": "toolu_1", "content": "hello"},
               {"role": "tool", "tool_call_id": "toolu_2", "content": "world"}]
    model.chat(history, TOOLS)
    sent = Messages.bodies[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == {"type": "thinking", "thinking": "Read a.txt first.", "signature": "sig-1"}
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["toolu_1", "toolu_2"]
    assert "fallbacks" not in Messages.bodies[1]                          # not a model that takes it


def test_a_turn_written_by_another_model_goes_as_text_and_tool_use():
    system, msgs = claude.to_messages([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "On it.", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "found"}])
    assert system == "sys"
    assert msgs[1] == {"role": "assistant", "content": [{"type": "text", "text": "On it."},
                                                         {"type": "tool_use", "id": "c1", "name": "search",
                                                          "input": {"q": "x"}}]}


def test_after_a_fallback_the_declined_part_is_echoed_without_thinking_or_tool_calls():
    native = [{"type": "thinking", "thinking": "a", "signature": "s1"}, {"type": "text", "text": "Start"},
              {"type": "tool_use", "id": "t0", "name": "x", "input": {}},
              {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}},
              {"type": "thinking", "thinking": "b", "signature": "s2"}, {"type": "text", "text": " and go on."},
              {"type": "tool_use", "id": "t1", "name": "y", "input": {"k": 1}}]
    _, msgs = claude.to_messages([{"role": "user", "content": "q"}, {"role": "assistant", "content": "", "claude": native}])
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "fallback", "thinking", "text", "tool_use"]

    class Final:
        content = []
        stop_reason = "tool_use"

        class usage:
            input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 1, 2, 0, 0

    class Block(dict):
        def to_dict(self):
            return dict(self)
    Final.content = [Block(b) for b in native]
    reply = claude.to_reply(Final)
    assert [c["id"] for c in reply.tool_calls] == ["t1"]                  # only the call the fallback model made


def test_a_refused_parameter_is_dropped_for_the_session(endpoint):
    Messages.refuse = ["fallbacks", "block_binding"]
    model = claude.ClaudeModel("claude-opus-5-5", api_key="k")
    assert model.chat([{"role": "user", "content": "go"}], TOOLS).tool_calls
    assert model.refused == {"fallbacks", "block_binding"} and len(Messages.bodies) == 3
    assert "fallbacks" not in Messages.bodies[2] and "block_binding" not in Messages.bodies[2]["thinking"]
    model.chat([{"role": "user", "content": "again"}], TOOLS)
    assert len(Messages.bodies) == 4                                      # no retry: remembered


def test_reasoning_levels(endpoint):
    model = claude.ClaudeModel("claude-opus-5-5", api_key="k")
    model.chat([{"role": "user", "content": "go"}])
    model.chat([{"role": "user", "content": "go"}], think=False)
    model.effort = "medium"
    model.chat([{"role": "user", "content": "go"}])
    assert [b.get("output_config") for b in Messages.bodies] == [None, {"effort": "low"}, {"effort": "medium"}]
    assert all(b["thinking"]["type"] == "adaptive" for b in Messages.bodies)   # never disabled: Opus 5.5 400s on it


def test_a_cut_answer_runs_no_tool_and_a_refusal_says_so(endpoint):
    cut = [dict(e) for e in THINK_TOOL]
    cut[-2] = {"type": "message_delta", "delta": {"stop_reason": "max_tokens", "stop_sequence": None},
               "usage": {"output_tokens": 9}}
    Messages.events = cut
    reply = claude.ClaudeModel("claude-opus-5-5", api_key="k").chat([{"role": "user", "content": "go"}], TOOLS)
    assert reply.finish == "length" and reply.tool_calls == []
    text = [dict(e, index=0) for e in THINK_TOOL[5:8]]
    Messages.events = [THINK_TOOL[0], *text,
                       {"type": "message_delta", "delta": {"stop_reason": "refusal", "stop_sequence": None,
                                                           "stop_details": {"type": "refusal", "category": "cyber",
                                                                            "explanation": None}},
                        "usage": {"output_tokens": 3}},
                       {"type": "message_stop"}]
    reply = claude.ClaudeModel("claude-opus-5-5", api_key="k").chat([{"role": "user", "content": "go"}])
    assert reply.finish == "refusal" and "declined" in reply.content


def test_errors_other_than_400_fail_with_the_message(endpoint, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    with pytest.raises(ModelError, match="does not answer"):
        claude.ClaudeModel("claude-opus-5-5", api_key="k", timeout=2).chat([{"role": "user", "content": "go"}])


def test_other_engines_do_not_get_claudes_blocks():
    from mafdet.agent.model import plain
    assert plain({"role": "assistant", "content": "x", "claude": [{"type": "thinking"}]}) == \
        {"role": "assistant", "content": "x"}
