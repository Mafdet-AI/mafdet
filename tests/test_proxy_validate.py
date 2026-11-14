"""Validation of the notes on Enter (2026-11-14), both off by default: the model's one-token check
(think.VERIFY_PROBE) and k independent reasonings ahead compared on their answers (think.BRANCHES)."""
import json

from mafdet.proxy import think
from mafdet.proxy.server import ProxyState
from proxy_fakes import FakeEngine
from test_proxy_think import SYSTEM, TEXT, events_of, post, start, wait_until

REQUEST = {"model": "fake-model", "stream": True, "conversation_id": "c1",
           "messages": [SYSTEM, {"role": "user", "content": TEXT}]}


def _content(raw: bytes) -> str:
    return "".join(e["choices"][0]["delta"].get("content") or "" for e in events_of(raw))


# ---- off: nothing changes -------------------------------------------------------------------
def test_with_both_off_no_probe_and_no_branch_slot():
    server, port, state, engine = start(think_after=6, n_slots=3)
    try:
        assert state.branch_slots == [] and state.no_branch_reason == ""
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert sess.branches == []
        assert wait_until(lambda: sess.thinker.finished)
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert status == 200 and _content(raw).strip() == "The answer is 42"
        assert not hasattr(engine, "probe_prompts")                      # never asked
        assert state.counts["thought_ahead"] == 1
        assert wait_until(lambda: len(state.recent) > 0)        # recorded just after the answer
        row = state.recent[-1]
        assert not ({"verify_p_yes", "branches_agreed", "fallback"} & set(row))
        assert "verified_dropped" not in state.stats()["sessions"][0]["thinking"]
    finally:
        server.shutdown()


# ---- VERIFY_PROBE ---------------------------------------------------------------------------
def test_a_low_p_yes_drops_the_notes(monkeypatch):
    monkeypatch.setattr(think, "VERIFY_PROBE", True)
    server, port, state, engine = start(think_after=6, probs_answer={" yes": 0.1, " no": 0.9, " maybe": 0.3})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.finished)
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert status == 200
        assert state.counts["thought_ahead"] == 0 and engine.chats == 1       # from scratch
        assert sess.thinker.verified_dropped == 1
        assert abs(sess.thinker.last_verify - 0.1) < 1e-9                    # yes / (yes + no)
        assert wait_until(lambda: len(state.recent) > 0)        # recorded just after the answer
        assert abs(state.recent[-1]["verify_p_yes"] - 0.1) < 1e-9
        assert state.last_turn.get("verified_dropped") is True
    finally:
        server.shutdown()


def test_a_high_p_yes_keeps_the_notes_and_the_probe_stays_out_of_the_answer(monkeypatch):
    monkeypatch.setattr(think, "VERIFY_PROBE", True)
    server, port, state, engine = start(think_after=6, probs_answer={"Yes": 0.5, " yes": 0.3, " No": 0.2})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        assert wait_until(lambda: sess.thinker.finished)
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert _content(raw).strip() == "The answer is 42"
        assert state.counts["thought_ahead"] == 1 and engine.chats == 0
        assert abs(sess.thinker.last_verify - 0.8) < 1e-9
        sent = state.frame.sent
        probe = engine.probe_prompts[-1]
        answer_prompt = engine.streamed
        # the probe = the answer's prompt without SENT, then the probe text
        assert probe[:len(answer_prompt) - len(sent)] == answer_prompt[:-len(sent)]
        assert probe[len(answer_prompt) - len(sent):] == state.frame.tokenize(think.VERIFY_TEXT)
        # and the answer's prompt never holds the probe
        check = state.frame.tokenize("Check:")[0]
        assert check not in answer_prompt
        assert answer_prompt[-len(sent):] == sent
    finally:
        server.shutdown()


def test_no_probabilities_keeps_the_notes(monkeypatch):
    monkeypatch.setattr(think, "VERIFY_PROBE", True)
    server, port, state, engine = start(think_after=6, probs_answer={" maybe": 1.0})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        assert wait_until(lambda: state.session("c1").thinker.finished)
        post(port, "/v1/chat/completions", REQUEST)
        assert state.counts["thought_ahead"] == 1 and state.last_turn["verify_p_yes"] is None
    finally:
        server.shutdown()


# ---- BRANCHES -------------------------------------------------------------------------------
def _branches_ready(state):
    sess = state.session("c1")
    return sess, wait_until(lambda: sess.thinker.finished and all(t.finished for t, _ in sess.branches))


def test_two_branches_that_agree_answer_from_their_notes(monkeypatch):
    monkeypatch.setattr(think, "BRANCHES", 2)
    monkeypatch.setattr(think, "RECORD_TURNS", True)
    server, port, state, engine = start(think_after=6, n_slots=3)
    try:
        assert state.side_slot == 1 and state.branch_slots == [2]
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready and len(sess.branches) == 1
        thinker, eng = sess.branches[0]
        assert eng.slot == 2 and eng is not engine
        assert thinker.reasoned > 0                                          # notes of its own
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert _content(raw).strip() == "The answer is 42"
        assert state.counts["thought_ahead"] == 1 and engine.chats == 0
        assert state.last_turn["branches_agreed"] is True
        assert state.last_turn["branch_answers"] == ["42", "42"]
        assert "fact" in state.last_turn["notes_text"] and len(state.last_turn["branch_notes"]) == 1
        assert wait_until(lambda: len(state.recent) > 0)        # recorded just after the answer
        assert state.recent[-1]["branches_agreed"] is True
        assert wait_until(lambda: thinker.notes == [])                       # a new turn: all branches reset
    finally:
        server.shutdown()


def test_two_branches_that_disagree_answer_from_scratch(monkeypatch):
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3,
                                        final_by_slot={2: "other reasoning </think> The answer is 7"})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert status == 200
        assert state.counts["thought_ahead"] == 0 and engine.chats == 1       # the plain request
        assert _content(raw).startswith("echo: ")
        assert state.last_turn["branch_answers"] == ["42", "7"]
        assert state.last_turn["fallback"] == "the branches disagree"
        assert wait_until(lambda: len(state.recent) > 0)        # recorded just after the answer
        assert state.recent[-1]["branches_agreed"] is False
    finally:
        server.shutdown()


def test_branches_need_slots(monkeypatch):
    monkeypatch.setattr(think, "BRANCHES", 3)
    server, port, state, engine = start(n_slots=2)
    try:
        assert state.branch_slots == [] and "need 4 slots" in state.no_branch_reason
    finally:
        server.shutdown()


def test_branch_seeds_differ_only_with_branches(monkeypatch):
    seen = []

    class Recording(FakeEngine):
        def generate(self, ids, *, n_predict, sampling=None, logit_bias=None, abort=None):
            seen.append(dict(sampling or {}))
            return super().generate(ids, n_predict=n_predict, sampling=sampling, logit_bias=logit_bias, abort=abort)
    from mafdet.proxy.probe import probe
    from mafdet.proxy.server import make_server
    import threading
    for branches in (1, 2):
        seen.clear()
        monkeypatch.setattr(think, "BRANCHES", branches)
        engine = Recording()
        engine.n_slots = 3
        state = ProxyState(engine, probe(engine, url="http://127.0.0.1:1", model="m"), "m", debounce_ms=20,
                           min_new=4, think_ahead=True)
        server = make_server(state, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            post(server.server_address[1], "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
            assert wait_until(lambda: len(seen) >= 2)
            if branches == 1:
                assert all("seed" not in s for s in seen)
            else:
                assert all("seed" in s for s in seen) and len({s["seed"] for s in seen}) > 1
        finally:
            server.shutdown()


def test_final_answer_compares_numbers_then_text():
    assert think.final_answer("So each pays 27.") == "27"
    assert think.final_answer("Total: 1,800 metres") == "1800"
    assert think.final_answer("It is 51.0") == "51"
    assert think.final_answer("  Paris  is the Capital ") == "paris is the capital"
    assert json.dumps(think.final_answer("")) == '""'


def test_a_comprehension_branch_reads_with_its_own_frame(monkeypatch):
    """BRANCH_FRAME = "comprehend": the other branch opens its notes with the read-only frame and answers after
    its own SENT; the conversation's branch keeps the usual frame."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    monkeypatch.setattr(think, "BRANCH_FRAME", "comprehend")
    server, port, state, engine = start(think_after=6, n_slots=3)
    try:
        assert state.branch_frame is not state.frame
        assert state.branch_frame.seed == engine.tokenize(think.COMPREHEND_SEED)
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        thinker, _ = sess.branches[0]
        assert thinker.notes[:len(state.branch_frame.seed)] == state.branch_frame.seed
        assert sess.thinker.notes[:len(state.frame.seed_en)] == state.frame.seed_en      # English: its own frame
        status, raw = post(port, "/v1/chat/completions", REQUEST)
        assert status == 200 and state.counts["thought_ahead"] == 1
    finally:
        server.shutdown()


def test_times_are_compared_whole():
    """Two branches at 22:55 and 21:55 "agreed" on 55 (held-out problem 27, 2026-11-14)."""
    assert think.final_answer("So the answer is 22:55.") == "22:55"
    assert think.final_answer("ends at 21:55") != think.final_answer("ends at 22:55")
    assert think.final_answer("each pays 27 euros") == "27"


def test_a_looping_thinker_starts_over_with_loop_restart(monkeypatch):
    """LOOP_RESTART: a loop clears the notes and the reasoning starts over (at most LOOP_RESTARTS_MAX times
    a turn), instead of leaving the branch without notes for the rest of the turn."""
    monkeypatch.setattr(think, "LOOP_RESTART", True)
    monkeypatch.setattr(think, "BRANCHES", 2)                              # restarts only where branches check
    monkeypatch.setattr(think, "looping", lambda text: True)              # every chunk "loops"
    server, port, state, engine = start()
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess = state.session("c1")
        th = sess.thinker
        assert wait_until(lambda: th.looped, timeout=5.0)
        assert th.restarts == think.LOOP_RESTARTS_MAX                      # it started over twice, then gave up
        th.reset()
        assert th.restarts == 0 and not th.looped
    finally:
        server.shutdown()


def test_one_branch_does_not_restart_and_think_ahead_means_two(monkeypatch):
    """Without a second branch to check it, a looping reasoning is dropped as before (no restart); and
    `mafdet proxy --think-ahead` means two branches unless --think-branches says otherwise."""
    from mafdet.cli import build_parser
    from mafdet.proxy.launch import think_branches
    monkeypatch.setattr(think, "BRANCHES", 1)
    monkeypatch.setattr(think, "looping", lambda text: True)
    server, port, state, engine = start()
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        th = state.session("c1").thinker
        assert wait_until(lambda: th.looped, timeout=5.0) and th.restarts == 0
    finally:
        server.shutdown()
    parse = build_parser().parse_args
    assert think_branches(parse(["proxy", "--think-ahead"])) == 2
    assert think_branches(parse(["proxy", "--think-ahead", "--think-branches", "1"])) == 1
    assert think_branches(parse(["proxy"])) == 1
    assert think_branches(parse(["proxy", "--think-ahead", "--exact"])) == 1


def _stream_timed(port, body):
    """POST a streamed chat and read it event by event: [(seconds since perf_counter epoch, event dict)]."""
    import http.client
    import time as _t
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/v1/chat/completions", body=json.dumps(body).encode(),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    out = []
    while True:
        line = resp.readline()
        if not line:
            break
        if line.startswith(b"data: ") and b"[DONE]" not in line:
            out.append((_t.perf_counter(), json.loads(line[6:])))
    conn.close()
    return out


def test_streamed_branches_show_the_reasoning_live_and_the_answer_once_they_agree(monkeypatch):
    """Branch 0's reasoning reaches the client while the other branch is still answering; its answer only
    after both are done and agree."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    monkeypatch.setattr(engine, "stream_delay_by_slot", {2: 0.05})          # the other branch is slower
    monkeypatch.setattr(engine, "stream_done", {})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        events = _stream_timed(port, REQUEST)
        deltas = [(t, e["choices"][0]["delta"], e["choices"][0]["finish_reason"]) for t, e in events]
        first_reasoning = next(t for t, d, _ in deltas if d.get("reasoning_content"))
        first_content = next(t for t, d, _ in deltas if d.get("content"))
        assert first_reasoning < engine.stream_done[2] <= first_content      # live reasoning, held answer
        assert "".join(d.get("content") or "" for _, d, _ in deltas).strip() == "The answer is 42"
        assert sum(1 for _, d, _ in deltas if "role" in d) == 1
        assert sum(1 for _, _, f in deltas if f) == 1 and deltas[-1][2] == "stop"
        assert len({e["id"] for _, e in events}) == 1
        assert state.last_turn["branches_agreed"] is True and state.last_turn.get("streamed_reasoning") is True
        assert state.counts["thought_ahead"] == 1 and engine.chats == 0
    finally:
        server.shutdown()


def test_streamed_branches_that_disagree_say_so_then_stream_from_scratch(monkeypatch):
    """Disagreement: branch 0's reasoning stays, a line says the answers disagree, then the from-scratch answer
    under the same id, with one role and one finish."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    monkeypatch.setattr(engine, "final_by_slot", {2: "other reasoning </think> The answer is 7"})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        events = [e for _, e in _stream_timed(port, REQUEST)]
        deltas = [e["choices"][0]["delta"] for e in events]
        reasoning = [d.get("reasoning_content") for d in deltas if d.get("reasoning_content")]
        note_at = next(i for i, d in enumerate(deltas) if d.get("reasoning_content") == think.DISAGREE_NOTE)
        assert len(reasoning) >= 2 and reasoning.index(think.DISAGREE_NOTE) > 0       # branch 0's reasoning first
        content = "".join(d.get("content") or "" for d in deltas)
        assert content.startswith("echo: ") and "42" not in content                   # the from-scratch answer
        assert all(not d.get("content") for d in deltas[:note_at])                     # nothing of branch 0's answer
        assert sum(1 for d in deltas if "role" in d) == 1
        assert sum(1 for e in events if e["choices"][0]["finish_reason"]) == 1
        assert len({e["id"] for e in events}) == 1
        assert engine.chats == 1 and state.last_turn["fallback"] == "the branches disagree"
        assert state.counts["thought_ahead"] == 0
    finally:
        server.shutdown()


def test_unstreamed_branches_are_unchanged(monkeypatch):
    """A request without stream: the branches are compared before anything is sent, one JSON answer."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        status, raw = post(port, "/v1/chat/completions", dict(REQUEST, stream=False))
        msg = json.loads(raw)["choices"][0]["message"]
        assert status == 200 and msg["content"].strip() == "The answer is 42" and msg.get("reasoning_content")
        assert "streamed_reasoning" not in state.last_turn and state.last_turn["branches_agreed"] is True
    finally:
        server.shutdown()
