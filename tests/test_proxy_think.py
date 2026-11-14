"""Thinking ahead (T2): notes written while the user types, the answer on Enter from them."""
import http.client
import json
import threading
import time

from mafdet.proxy import think
from mafdet.proxy.probe import probe
from mafdet.proxy.server import ProxyState, make_server
from proxy_fakes import FakeEngine

SYSTEM = {"role": "system", "content": "be brief"}
TEXT = "how many apples are left if Luca buys three"


def start(**engine_attrs):
    engine = FakeEngine()
    for k, v in engine_attrs.items():
        setattr(engine, k, v)
    profile = probe(engine, url="http://127.0.0.1:1", model="fake-model")
    state = ProxyState(engine, profile, "fake-model", debounce_ms=20, min_new=4, think_ahead=True)
    server = make_server(state, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], state, engine


def post(port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, raw


def wait_until(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def events_of(raw: bytes) -> list[dict]:
    out = []
    for line in raw.split(b"\n"):
        if line.startswith(b"data: ") and b"[DONE]" not in line:
            out.append(json.loads(line[6:]))
    return out


def test_the_model_reasons_while_the_user_types():
    server, port, state, engine = start()
    try:
        assert state.frame is not None
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.reasoned >= 8)
        notes = sess.thinker.notes
        seed = state.frame.seed_en                                      # an English message: the English example
        assert notes[:len(seed)] == seed and seed != state.frame.seed   # the frame opens the reasoning
        # each chunk runs on the draft's prompt followed by the notes so far
        assert engine.gen_prompts[0][:len(sess.latest)] == sess.latest
    finally:
        server.shutdown()


def test_plain_read_ahead_stays_off_while_it_reasons():
    # A warm would leave the slot holding the prompt alone and throw the notes out of the cache.
    server, port, state, engine = start()
    try:
        engine.warms = 0
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.reasoned >= 4)
        assert sess.flush() is None
        assert engine.warms == 0
    finally:
        server.shutdown()


def test_on_enter_the_answer_follows_the_notes():
    server, port, state, engine = start(think_after=6)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.finished)
        status, raw = post(port, "/v1/chat/completions",
                           {"model": "fake-model", "stream": True,
                            "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        assert status == 200
        events = events_of(raw)
        reasoning = "".join(e["choices"][0]["delta"].get("reasoning_content") or "" for e in events)
        content = "".join(e["choices"][0]["delta"].get("content") or "" for e in events)
        assert "fact1" in reasoning and "more reasoning" in reasoning      # the notes, then what followed
        assert content.strip() == "The answer is 42"
        assert events[-1]["choices"][0]["finish_reason"] == "stop"
        # the prompt was the draft's, then the notes, then SENT
        assert engine.streamed[-len(state.frame.sent):] == state.frame.sent
        assert state.counts["thought_ahead"] == 1
        assert engine.chats == 0                                          # not passed through
        # the conversation is kept, and the next turn starts new notes
        assert wait_until(lambda: sess.history is not None)
        assert sess.history[-1]["content"].strip() == "The answer is 42"
        assert sess.thinker.notes == []
    finally:
        server.shutdown()


def test_a_request_with_thinking_off_goes_through_as_it_is():
    server, port, state, engine = start()
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.session("c1").thinker.reasoned >= 4)
        post(port, "/v1/chat/completions", {"model": "fake-model", "chat_template_kwargs": {"enable_thinking": False},
                                            "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        assert engine.chats == 1 and state.counts["thought_ahead"] == 0
    finally:
        server.shutdown()


def test_without_a_reasoning_template_think_ahead_is_off():
    server, port, state, engine = start(thinking=False)
    try:
        assert state.frame is None
        assert state.session("c1").thinker is None
    finally:
        server.shutdown()


def test_a_non_streamed_request_gets_one_message():
    server, port, state, engine = start(think_after=4)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.session("c1").thinker.finished)
        status, raw = post(port, "/v1/chat/completions",
                           {"model": "fake-model", "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        msg = json.loads(raw)["choices"][0]["message"]
        assert msg["content"].strip() == "The answer is 42" and "fact1" in msg["reasoning_content"]
    finally:
        server.shutdown()


def test_qwen3_tool_calls_become_openai_tool_calls():
    calls = think.ToolCalls()
    shown = ""
    for piece in ["Let me check. <tool", "_call>\n{\"name\": \"get_time\", ", "\"arguments\": {\"tz\": \"UTC\"}}\n</tool_",
                  "call>"]:
        content, done = calls.feed(piece)
        shown += content
    shown += calls.flush()
    assert shown == "Let me check. "
    assert len(calls.calls) == 1
    assert calls.calls[0]["function"]["name"] == "get_time"
    assert json.loads(calls.calls[0]["function"]["arguments"]) == {"tz": "UTC"}


def test_a_tool_call_ends_the_answer_with_tool_calls():
    server, port, state, engine = start(think_after=4, final_text="ok </think> <tool_call> "
                                                                   "{\"name\": \"get_time\", \"arguments\": {}} </tool_call>")
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.session("c1").thinker.finished)
        status, raw = post(port, "/v1/chat/completions",
                           {"model": "fake-model", "stream": True, "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        events = events_of(raw)
        calls = [c for e in events for c in (e["choices"][0]["delta"].get("tool_calls") or [])]
        assert calls and calls[0]["function"]["name"] == "get_time"
        assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        server.shutdown()


def test_the_prompt_is_rendered_again_when_the_request_has_other_tools():
    # Drafts rendered before the proxy knew the front end's tools (first request after a restart):
    # the answer must follow the prompt the request asks for, tools included.
    server, port, state, engine = start(think_after=4)
    tools = [{"type": "function", "function": {"name": "get_time", "parameters": {}}}]
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.session("c1").thinker.finished)
        post(port, "/v1/chat/completions", {"model": "fake-model", "stream": True, "tools": tools,
                                            "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        with_tools = engine.render([SYSTEM, {"role": "user", "content": TEXT}], None, tools)
        assert engine.streamed[:len(with_tools)] == with_tools
    finally:
        server.shutdown()


def test_enter_stops_the_chunk_in_flight():
    # On a slow model a chunk of 32 tokens takes seconds: Enter must not wait for it.
    server, port, state, engine = start(slow_chunks=5.0)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.cache.warm_lock.locked())          # a chunk is running
        t0 = time.perf_counter()
        state.begin_generation()
        took = time.perf_counter() - t0
        state.end_generation()
        assert took < 1.0, f"Enter waited {took:.1f} s for the chunk"
    finally:
        server.shutdown()


class OpenAIOnlyEngine(FakeEngine):
    """An engine with a chat endpoint but no raw completion (as Ignis today)."""
    generate = stream = detokenize = None


def test_an_engine_without_raw_completion_keeps_reading_ahead_only():
    engine = OpenAIOnlyEngine()
    profile = probe(engine, url="http://127.0.0.1:1", model="fake-model")
    state = ProxyState(engine, profile, "fake-model", think_ahead=True)
    assert state.frame is None
    assert "raw completion" in state.no_think_reason
    assert state.session("c1").thinker is None
    assert state.session("c1").tier == "T1"                 # reading ahead still on


def test_a_looping_reasoning_is_stopped_and_its_notes_dropped_on_enter():
    """The same line again and again (Qwen3-4B on half a word problem, 2026-11-14): no more chunks, and on
    Enter the request goes through as it is instead of answering from the loop."""
    assert think.looping("- 18 p\nSo the answer is:\n18\nx\nSo the answer is:\n18\nSo the answer is:")
    assert not think.looping("So the answer is:\n18\n18\n18\nfact one\nfact two")   # short lines don't count
    assert not think.looping("- Luca: 25 euros\n- 3 notebooks: 12 euros\n- pen: 2 euros, left 11")
    server, port, state, engine = start()
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.reasoned >= 8)
        with sess.thinker.lock:
            sess.thinker.written += "\nSo the answer is: 18\nSo the answer is: 18\nSo the answer is: 18"
            sess.thinker.looped = think.looping(sess.thinker.written)
        assert sess.thinker.looped
        status, raw = post(port, "/v1/chat/completions", {"model": "fake-model", "conversation_id": "c1",
                                                           "stream": True,
                                                           "messages": [SYSTEM, {"role": "user", "content": TEXT}]})
        assert status == 200
        assert state.counts["thought_ahead"] == 0 and sess.thinker.dropped == 1
        assert state.stats()["sessions"][0]["thinking"]["dropped"] == 1
    finally:
        server.shutdown()


def test_the_example_line_follows_the_message_language_and_waits_until_it_is_known():
    """The frame's example line is Italian (as measured in the app); an English message gets it in English
    (A1, 2026-11-14: the 4B copied "mass of the Moon" into English notes). No reasoning before a word tells
    the language, up to LANG_WAIT_WORDS words."""
    assert think.language("how many apples are left") == "en"
    assert think.language("Luca ha 25 euro, compra 3 quaderni") == "it"
    assert think.language("Marta reads 18 pages") is None
    server, port, state, engine = start()
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": "Marta reads 18 pages"})
        sess = state.session("c1")
        time.sleep(0.4)
        assert sess.thinker.reasoned == 0                                # the language is not known yet
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": "Marta reads 18 pages a day for 6 days"})
        assert wait_until(lambda: sess.thinker.reasoned >= 4)
        assert sess.thinker.notes[:len(state.frame.seed_en)] == state.frame.seed_en
        post(port, "/v1/drafts", {"conversation_id": "c2", "text": "Luca ha 25 euro e compra tre quaderni"})
        s2 = state.session("c2")
        assert wait_until(lambda: s2.thinker.reasoned >= 4)
        assert s2.thinker.notes[:len(state.frame.seed)] == state.frame.seed
    finally:
        server.shutdown()


def test_the_switches_against_early_conclusions_shape_the_bias(monkeypatch):
    """END_BAN forbids </think> while reasoning ahead, ANSWER_BIAS pushes down the answer words; both off by
    default (as measured before 2026-11-14), each turned on alone by the bench (--set-think)."""
    frame = think.Frame(FakeEngine())
    assert frame.bias() == frame.meta_bias
    monkeypatch.setattr(think, "END_BAN", True)
    monkeypatch.setattr(think, "ANSWER_BIAS", -4.0)
    bias = frame.bias()
    assert [frame.think_end, -100.0] in bias
    assert all([t, -4.0] in bias for t in frame.answer_ids)
