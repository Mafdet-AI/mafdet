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
