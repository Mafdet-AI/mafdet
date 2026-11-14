"""Validation of the notes on Enter (2026-11-14), both off by default: the model's one-token check
(think.VERIFY_PROBE) and k independent reasonings ahead compared on their answers (think.BRANCHES)."""
import json
import time

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


def test_final_answer_reads_a_clear_final_value_only():
    """The independent review's examples (2026-11-14): numbers whole, fractions, am/pm, scientific notation,
    trailing zeros; prose with a stray number, and an empty answer, have no final value."""
    f = think.final_answer
    assert f("1,234,567") != f("9,999,567") and f("1,234,567") == "1234567"
    assert f("3/4") != f("1/4") and f("6/8") == "3/4"
    assert f("9:05 AM") != f("9:05 PM") and f("09:05") == f("9:05")
    assert f("7.35e22") != f("1e22")
    assert f("12.5") == f("12.50") == "12.5" and f("It is 51.0") == "51"
    assert f("**42** (step 3)") == f("42") == "42"
    assert f("...since 987") is None and f("Founded 987") is None
    assert f("") is None and f("  Paris  is the Capital ") is None
    assert f("So each pays 27.") == "27" and f("Answer: 1,800 metres") == "1800"
    assert f("The final value is \\boxed{27} euros.") == "27"
    assert f("Each friend pays **27** euros.") == "27" and f("The answer is 42 ") == "42"
    assert f("The film ends at 21:55.") == "21:55"


def test_compare_branches_prose_mixed_failed_and_tool_calls():
    def chunks(text=None, calls=None, mark=None):
        out = [{"choices": [{"delta": {"role": "assistant", "content": None}, "finish_reason": None}]}]
        if text is not None:
            out.append({"choices": [{"delta": {"content": text}, "finish_reason": None}]})
        if calls:
            out.append({"choices": [{"delta": {"tool_calls": calls}, "finish_reason": None}]})
        out.append({"choices": [{"delta": {}, "finish_reason": "stop"}], **({"x_mafdet": mark} if mark else {})})
        return out
    agree = think.compare_branches
    assert agree([chunks("The answer is 27"), chunks("**27** euros")])[0] is True
    assert agree([chunks("The answer is 27"), chunks("The answer is 24")])[0] is False
    assert agree([chunks("Rome was founded long ago."), chunks("It is an old city.")])[0] is None    # prose
    assert agree([chunks("The answer is 27"), chunks("It depends on the tip.")]) [0] is False       # mixed
    assert agree([chunks("The answer is 27"), chunks("")])[2] == "a branch failed"                  # empty
    assert agree([chunks("The answer is 27"), None])[0] is False                                     # no answer
    assert agree([chunks("The answer is 27"), chunks("The answer is 27", mark={"error": True})])[0] is False
    assert agree([chunks("The answer is 27"), chunks("The answer is 27", mark={"cancelled": True})])[0] is False
    call = [{"index": 0, "id": "c", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    assert agree([chunks(calls=call), chunks(calls=call)])[0] is True


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
    assert think.final_answer("It ends at 21:55") != think.final_answer("It ends at 22:55")
    assert think.final_answer("each pays 27 euros") == "27"


def test_a_looping_thinker_starts_over_with_loop_restart(monkeypatch):
    """LOOP_RESTART: a loop clears the notes and the reasoning starts over (at most LOOP_RESTARTS_MAX times
    a turn), instead of leaving the branch without notes for the rest of the turn."""
    monkeypatch.setattr(think, "LOOP_RESTART", True)
    monkeypatch.setattr(think, "BRANCHES", 2)                              # restarts only where branches check
    monkeypatch.setattr(think, "looping", lambda text: True)              # every chunk "loops"
    server, port, state, engine = start(n_slots=3)                        # a second branch really there
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


# ---- the independent review's findings (2026-11-14) ----------------------------------------------------------

def test_an_errored_branch_counts_as_a_disagreement(monkeypatch):
    """An engine error on a branch's stream (a full context) is not an agreed empty answer: from scratch,
    streamed and not."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    for stream in (False, True):
        server, port, state, engine = start(think_after=6, n_slots=3)
        monkeypatch.setattr(engine, "error_by_slot", {2: True})
        try:
            post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
            sess, ready = _branches_ready(state)
            assert ready
            status, raw = post(port, "/v1/chat/completions", dict(REQUEST, stream=stream))
            assert status == 200 and engine.chats == 1                       # worked out from scratch
            assert state.last_turn["branches_agreed"] is False
            assert state.last_turn["fallback"] == "a branch failed" and state.counts["thought_ahead"] == 0
            assert b"x_mafdet" not in raw                                     # no marker reaches the client
        finally:
            server.shutdown()


def test_branch_seeds_and_restarts_follow_the_real_branch_count(monkeypatch):
    """The global asks for 2 branches, the engine has room for 1: no seeds, no restart (nothing would check a
    restarted reasoning)."""
    seen = []

    class Recording(FakeEngine):
        def generate(self, ids, *, n_predict, sampling=None, logit_bias=None, abort=None):
            seen.append(dict(sampling or {}))
            return super().generate(ids, n_predict=n_predict, sampling=sampling, logit_bias=logit_bias, abort=abort)
    from mafdet.proxy.probe import probe
    from mafdet.proxy.server import make_server
    import threading
    monkeypatch.setattr(think, "BRANCHES", 2)
    monkeypatch.setattr(think, "looping", lambda text: True)
    engine = Recording()
    engine.n_slots = 2                                                    # conversation + side: no branch slot
    state = ProxyState(engine, probe(engine, url="http://127.0.0.1:1", model="m"), "m", debounce_ms=20,
                       min_new=4, think_ahead=True)
    server = make_server(state, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert state.branch_slots == [] and state.no_branch_reason
        post(server.server_address[1], "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        th = state.session("c1").thinker
        assert th.branches == 1
        assert wait_until(lambda: th.looped, timeout=5.0) and th.restarts == 0
        assert seen and all("seed" not in s for s in seen)
    finally:
        server.shutdown()


def test_enter_waits_for_the_branch_chunks_with_abort_set():
    """Enter sets abort, waits for every branch's chunk lock (their own slots), then clears abort."""
    import threading
    from mafdet.proxy.session import SharedCache
    cache = SharedCache()
    other = threading.Lock()
    other.acquire()
    saw_abort = []

    def branch_chunk():                       # a chunk in flight on a branch: stops at its next token
        cache.abort.wait(2.0)
        saw_abort.append(cache.abort.is_set())
        other.release()
    threading.Thread(target=branch_chunk, daemon=True).start()
    cache.begin_generation(wait_for_warm=2.0, others=[other])
    assert saw_abort == [True] and not cache.abort.is_set() and other.acquire(timeout=0.1)


def test_a_chunk_from_before_a_reset_never_lands_in_the_next_turn(monkeypatch):
    """The turn is reset while a chunk is being written: the chunk is stale and dropped, even though the new
    turn's notes are empty then (the old check was skipped when they were)."""
    server, port, state, engine = start()
    orig = engine.generate
    calls = []

    def resetting(ids, **kw):
        calls.append(1)
        if len(calls) == 1:
            state.session("c1").thinker.reset()                            # Enter answered meanwhile
            return {"tokens": engine.tokenize("staleword"), "text": "staleword", "stop_type": "limit",
                    "timings": {}}
        return orig(ids, **kw)
    monkeypatch.setattr(engine, "generate", resetting)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        th = state.session("c1").thinker
        assert wait_until(lambda: len(calls) >= 3)                         # later chunks on the fresh notes
        assert "staleword" not in engine.detokenize(th.notes)
    finally:
        server.shutdown()


def test_a_slow_branch_is_cut_off_and_counts_as_a_disagreement(monkeypatch):
    """Once branch 0 has answered, the other gets BRANCH_SLACK times its time (at least BRANCH_MIN_EXTRA_S
    more); past it, it is cancelled and the answer is worked out from scratch."""
    monkeypatch.setattr(think, "BRANCHES", 2)
    monkeypatch.setattr(think, "BRANCH_MIN_EXTRA_S", 0.2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    monkeypatch.setattr(engine, "stream_delay_by_slot", {2: 0.5})           # ~3 s for its answer
    monkeypatch.setattr(engine, "stream_done", {})
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        t0 = time.perf_counter()
        status, raw = post(port, "/v1/chat/completions", dict(REQUEST, stream=False))
        assert time.perf_counter() - t0 < 2.0
        assert state.last_turn["fallback"] == "a branch failed" and engine.chats == 1
    finally:
        server.shutdown()


def test_branch_0_failing_mid_stream_sends_one_role_and_the_answer_from_scratch(monkeypatch):
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    monkeypatch.setattr(engine, "raise_after_by_slot", {0: 2})              # the conversation's slot drops
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        events = [e for _, e in _stream_timed(port, REQUEST)]
        deltas = [e["choices"][0]["delta"] for e in events]
        assert sum(1 for d in deltas if "role" in d) == 1
        assert sum(1 for e in events if e["choices"][0]["finish_reason"]) == 1
        assert "".join(d.get("content") or "" for d in deltas).startswith("echo: ")
        assert state.last_turn["fallback"] == "a branch failed"
    finally:
        server.shutdown()


def test_the_count_of_answers_from_notes_is_up_before_the_answer_goes_out(monkeypatch):
    """The bench reads counts["thought_ahead"] as soon as the stream ends: it must already be counted."""
    import http.client
    monkeypatch.setattr(think, "BRANCHES", 2)
    server, port, state, engine = start(think_after=6, n_slots=3)
    try:
        post(port, "/v1/drafts", {"conversation_id": "c1", "text": TEXT})
        sess, ready = _branches_ready(state)
        assert ready
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/v1/chat/completions", body=json.dumps(REQUEST).encode(),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        counted_at_content = None
        while True:
            line = resp.readline()
            if not line:
                break
            if line.startswith(b"data: ") and b"[DONE]" not in line:
                d = json.loads(line[6:])["choices"][0]["delta"]
                if d.get("content") and counted_at_content is None:
                    counted_at_content = state.counts["thought_ahead"]
        conn.close()
        assert counted_at_content == 1
    finally:
        server.shutdown()


def test_think_branches_needs_think_ahead_and_a_users_cache_type_is_kept(monkeypatch):
    from mafdet.cli import build_parser
    from mafdet.proxy import launch
    parse = build_parser().parse_args
    assert launch.think_branches(parse(["proxy", "--think-branches", "2"])) == 1          # no --think-ahead
    assert launch.think_branches(parse(["proxy", "--think-ahead", "--exact", "--think-branches", "3"])) == 1
    assert launch.think_branches(parse(["proxy", "--think-ahead", "--think-branches", "3"])) == 3
    made = []

    class FakeServer:
        def __init__(self, path, device, *, ctx, n_slots, port, extra_args, log_name, pool):
            made.append(tuple(extra_args))
    import mafdet.llama_backend as lb
    monkeypatch.setattr(lb, "LlamaServer", FakeServer)
    monkeypatch.setattr(lb, "pick_device", lambda d: "cuda")
    monkeypatch.setattr(launch, "resolve_model", lambda spec: ("m.gguf", "m", ()))
    for llama_args, q8 in (("", True), ("-ctv f16", False), ("--cache-type-k f16", False), ("-ctk q4_0", False)):
        launch.start_engine(parse(["proxy", "--think-ahead", "--llama-args=" + llama_args] if llama_args
                                  else ["proxy", "--think-ahead"]))
        extra = made[-1]
        assert (extra.count("q8_0") == 2) is q8, (llama_args, extra)


def test_the_bench_checks_only_and_counts_a_skipped_side():
    import argparse
    from mafdet.proxy import bench
    ns = argparse.Namespace(problems_file="", first=0, prompts=24, only="6,99", repeat=1)
    try:
        bench.pick_problems(ns)
        raise AssertionError("no error for problem 99")
    except SystemExit as e:
        assert "99" in str(e)
    ns.only = "6,23"
    ns.repeat = 2
    assert bench.pick_problems(ns)[2] == [6, 6, 23, 23]
    nan = float("nan")
    row = {"off_ttft": nan, "on_ttft": 0.1, "identical": False, "same_answer": False, "off_right": None,
           "on_finish": "stop", "off_finish": "skipped"}
    rows = [dict(row, on_right=True), dict(row, on_right=False), dict(row, on_right=None)]
    out = bench.summarise(rows)
    assert out["judged"] == 2 and out["on_right"] == 1 and out["off_mean_ms"] is None
    assert out["on_mean_ms"] == 100


def test_cmh_continuity_correction_is_floored_at_zero():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
    import think_stats
    orr, chi, p = think_stats.cmh([(1, 9, 1, 9)])
    assert chi == 0.0 and p == 1.0
