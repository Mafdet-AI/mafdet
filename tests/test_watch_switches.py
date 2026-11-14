"""The proxy's switches against conclusions on an unfinished question, ported to the app (mafdet/watch.py):
off by default and then the chunks are started as before; each one on shows in the chunk's bias, its stop
list, the notes kept at a stop, or no chunk once the question has begun."""
import threading
from types import SimpleNamespace

import pytest

from mafdet import watch
from mafdet.cli import build_parser
from mafdet.watch import Session

THINK, META, ANS = 3, 40, 41
WORDS = {"one": [10], "two": [11], "kept text": [70, 71], "\n- New words \"{}\": ": [80]}


class Recorder:
    """Stands for Stream: records how each chunk was started."""
    started: list = []

    def __init__(self, session, prompt, limit, text="", bias=None, slot=None, sampling=None):
        self.prompt, self.limit, self.text, self.bias, self.sampling = prompt, limit, text, bias, sampling
        Recorder.started.append(self)


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr(watch, "Stream", Recorder)
    Recorder.started = []
    watch.apply_switches("", reset=True)
    yield
    watch.apply_switches("", reset=True)


def session(text="Three friends split a 72 euro bill equally and leave a 9 euro tip."):
    s = Session.__new__(Session)
    s.args = SimpleNamespace(ctx=4096)
    s.think, s.answer_ahead, s.fmt, s.finished_text, s.ahead = True, [], "frame", None, 512
    s.think_cap, s.notes, s.notes_fresh, s.notes_text = 1024, [5, 6], 0, ""
    s.text, s.text_t, s.debounce = text, 0.0, 10.0
    s.meta_bias, s.answer_ids, s.think_end = [[META, -4.0]], [ANS], THINK
    s.sampling = dict(watch.THINK_SAMPLING)
    s.spec, s.chunk_t = None, 0.0
    s.tok, s.tok_lock = None, threading.Lock()
    s._encode = lambda tok, text: list(WORDS.get(text, [99]))
    s.bridge_text, s.last_bridge, s.seed_ids, s.awaiting = "", [], [5], False
    return s


def test_off_the_chunk_starts_as_before():
    s = session()
    s._start_chunk([1, 2, 3], s.text)
    (r,) = Recorder.started
    assert r.bias == [[META, -4.0]] and r.sampling is None and r.limit == watch.THINK_CHUNK


def test_end_ban_and_answer_bias_shape_the_chunk_bias():
    watch.apply_switches("END_BAN,ANSWER_BIAS=-8")
    s = session()
    s._start_chunk([1, 2, 3], s.text)
    bias = Recorder.started[0].bias
    assert [META, -4.0] in bias and [ANS, -8.0] in bias and [THINK, -100.0] in bias
    s.answer_ahead = [7]                       # writing the answer ahead: no reasoning bias at all
    s.spec = None
    s._start_chunk([1, 2, 3], s.text)
    assert Recorder.started[-1].bias is None


def test_stop_bridges_puts_the_stop_list_in_the_chunk_sampling():
    watch.apply_switches("STOP_BRIDGES")
    s = session()
    s._start_chunk([1, 2, 3], s.text)
    sampling = Recorder.started[0].sampling
    assert sampling["stop"] == watch.BRIDGE_STOPS and sampling["temperature"] == watch.THINK_SAMPLING["temperature"]
    assert "<|im_end|>" in sampling["stop"] and "\n- New words" in sampling["stop"]


def test_question_stop_starts_no_chunk_once_the_question_begins():
    assert watch.question_started("Three friends split a 72 euro bill. What")
    assert not watch.question_started("How much does the Moon weigh compared to")
    assert watch.question_started("Luca ha 25 euro, compra 3 quaderni. Quanti euro")
    watch.apply_switches("QUESTION_STOP")
    s = session()
    s._start_chunk([1], "Three friends split a 72 euro bill equally. What does each")
    assert Recorder.started == []
    s._start_chunk([1], "Three friends split a 72 euro bill equally and leave")
    assert len(Recorder.started) == 1


def test_a_chunk_stopped_on_a_stop_string_keeps_its_text_and_waits():
    watch.apply_switches("STOP_BRIDGES")
    s = session()
    spec = SimpleNamespace(tokens=[50, 51, 52], info={"stop_type": "word"}, text=s.text, text_out="kept text",
                           wait=lambda: None)
    s._absorb(spec, changed=False)
    assert s.notes == [5, 6, 70, 71] and s.answer_ahead == [] and s.finished_text == s.text
    s._start_chunk([1], s.text)
    assert Recorder.started == []              # stopped on this text: no chunk until it changes


def test_without_the_switch_a_word_stop_is_kept_as_written():
    s = session()
    spec = SimpleNamespace(tokens=[50, 51], info={"stop_type": "word"}, text=s.text, text_out="kept text",
                           wait=lambda: None)
    s._absorb(spec, changed=False)
    assert s.notes == [5, 6, 50, 51] and s.finished_text is None


def test_force_bridge_off_waits_for_two_words():
    s = session("Three friends split a")
    s.notes_fresh = 5
    s.bridge_text = "Three friends "
    s._bridge(1, min_words=True)               # one new whole word ("a" is still being typed): no bridge
    assert s.last_bridge == []
    s._bridge(1, min_words=False)
    assert s.notes_fresh == 0                  # the old behaviour: a bridge even for one word


def test_the_strike_flag_and_the_reset_between_configurations():
    args = build_parser().parse_args(["strike", "--ahead-switches", "QUESTION_STOP,END_BAN,STOP_BRIDGES,"
                                      "ANSWER_BIAS=-8,FORCE_BRIDGE=0"])
    got = watch.apply_switches(args.ahead_switches, reset=True)
    assert got == {"QUESTION_STOP": True, "END_BAN": True, "STOP_BRIDGES": True, "ANSWER_BIAS": -8.0,
                   "FORCE_BRIDGE": False}
    watch.apply_switches("", reset=True)       # the next Session without the flag: all off again
    assert (watch.QUESTION_STOP, watch.END_BAN, watch.STOP_BRIDGES, watch.ANSWER_BIAS, watch.FORCE_BRIDGE) == (
        False, False, False, 0.0, True)
    with pytest.raises(SystemExit):
        watch.apply_switches("NOPE")
