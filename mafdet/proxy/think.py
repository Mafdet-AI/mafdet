"""Thinking ahead (tier T2): the model reasons about the draft while the user types.

Not exact: the answer comes from reasoning started on a message that was not finished yet.  It is
opt-in (`--think-ahead`), for models whose chat template has a reasoning block (Qwen3), and only
for requests that leave thinking on.

The frame is the MAFDET app's, measured on Qwen3 (mafdet/watch.py, research/LOG.md), copied here
because the proxy does not import the app; sharing it is a separate change, as agreed in #2:
  * after the draft, inside the assistant's turn, SEED opens the reasoning: a list of material for
    the answer, one line per key term, name or number already typed;
  * the model writes it in short chunks (THINK_CHUNK tokens), each on the draft as it is then;
  * when the user typed more, a BRIDGE line names the new words, once enough new reasoning was
    written (BRIDGE_EVERY) or the reasoning had ended: it goes on from its notes, not from scratch;
  * on Enter: the final message, the notes, SENT ("the user has now sent the complete message"),
    and the model finishes reasoning from there and answers.
The proxy streams that answer back as an OpenAI chat completion: the reasoning (the notes, then
what follows) as `reasoning_content`, the answer as `content`, and Qwen3's <tool_call> blocks as
`tool_calls`.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import uuid
from typing import Iterable

SEED = ("<think>\nThe user is still typing. I will not wonder what they will ask, nor remark that the text is "
        "unfinished. While I wait, I collect material for the answer: for each key term, name or number already "
        "written, one line with what it is and the facts I know about it, or a partial result. For example, from "
        "\"Quanto pesa la Luna rispetto alla\" I would write \"- Luna (the Moon): mass 7.35e22 kg, radius "
        "1,737 km\".\n\n- ")
# The same frame with its example line in English, for a message in English. With the Italian line the 4B
# copied "mass of the Moon" into the notes of English word problems (A1, 2026-11-14); in the app, measured on
# Italian messages, the Italian line stays.
SEED_EN = SEED.replace("\"Quanto pesa la Luna rispetto alla\" I would write \"- Luna (the Moon): mass 7.35e22 kg",
                       "\"How much does the Moon weigh compared to\" I would write \"- Moon: mass 7.35e22 kg")
assert SEED_EN != SEED
# A message is taken as English when it has more of these words than of the Italian ones; a tie keeps the
# frame as measured (Italian). Words that are both (a, in, e...) are in neither list.
EN_WORDS = frozenset("the of and is are to for with how many much what does do has have was were each "
                     "if then there their they this that from by at his her its it you your".split())
IT_WORDS = frozenset("il lo la gli le un una di che è per con non sono ci quanti quante quanto ha del della "
                     "dei delle al alla allo ai nel nella si se poi come cosa sua suo loro questo questa".split())
BRIDGE ="\n- New words \"{new}\": "
BRIDGE_EDIT = "\n- The text was edited, so: "
BRIDGE_NEW_CHARS = 300
BRIDGE_MIN_WORDS = 2
SENT = "\n\nThe user has now sent the complete message. Let me reread it and answer it, using the material above.\n\n"
META_WORDS = ("user", "User", "incomplete", "unfinished", "typing", "typed", "intent", "intends", "asking",
              "wondering", "seems")
META_BIAS = -4.0
THINK_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0, "presence_penalty": 1.5}
THINK_MIN_WORDS = 3          # no reasoning on the first couple of words
THINK_CHUNK = 32             # tokens per chunk: short, so it follows the text as it is typed
BRIDGE_EVERY = 64            # new reasoning tokens before an edit earns a bridge
THINK_CAP = 1024             # longest reasoning written ahead
# A reasoning ahead that loops (Qwen3-4B, 2026-11-14: "So the answer is: 18" nine times on half a word
# problem, then 18 given as the answer to the whole one) is worse than none: from the LOOP_REPEATS-th time
# the same line of LOOP_MIN_CHARS or more comes back, the reasoning stops and Enter drops the notes.
# Against conclusions written on a question not finished yet (2026-11-14: in 31% of the notes "the answer is
# N" came before the question, and 3 of the 4 lost answers were such a conclusion kept after Enter). Each can
# be turned on alone (bench --set-think), measured, then made the default:
END_BAN = False              # the model may not close its reasoning (</think>) while reasoning ahead
ANSWER_WORDS = (" answer", "Answer", " final", "Final", " boxed", " Therefore")
ANSWER_BIAS = 0.0            # a logit bias on those words while reasoning ahead (0: off)
STOP_BRIDGES = False         # stop a chunk where the model starts a bridge line of its own ("- New words ...")
BRIDGE_STOPS = ["\n- New words", "\n- The text was edited",
                # with END_BAN the 4B looked for another way out and wrote the chat template's own markers
                # and a made-up next turn into its notes (2026-11-14): a stop there too
                "<|im_end|>", "<|endoftext|>", "<tool_call>", "<tool_response>", "\nHuman:", "\nUser:"]
FORCE_BRIDGE = True          # after the reasoning ended, bridge on the next new word (False: wait for 2 words,
                             # BRIDGE_MIN_WORDS, as for any bridge: no word-by-word bridges)
# What follows the notes on Enter. "reuse" is the frame measured in the app; "rederive" says the notes were
# written before the question was complete and asks for the answer worked out again from the whole message
# (the tip: notes that had decided "the tip is separate" were kept after Enter).
SENT_MODE = "reuse"
SENT_REDERIVE = ("\n\nThe user has now sent the complete message. The material above was written before the "
                 "question was complete, so any conclusion in it may be wrong. Let me reread the whole message, "
                 "work out exactly what it asks, and solve it step by step, using the numbers above only where "
                 "they fit.\n\n")
# Notes written on a question still being typed commit to a reading of it ("the tip is separate", 24; "add the
# tip", 33) that survives Enter, while the model alone, sampled at 0.6, was right 16 of 16 (2026-11-14). With
# QUESTION_STOP the reasoning ahead covers the setup and stops where the question begins after it: the
# question is read whole after Enter, with the material ready.
QUESTION_STOP = False
QUESTION_WORDS = frozenset("what how which who whom whose when where why quanto quanti quante quanta quale quali "
                           "cosa come chi dove quando perché perche".split())
SENTENCE_RX = re.compile(r"(?<=[.!?])\s+")
DROP_CONCLUSIONS = False    # on Enter, take out of the notes the lines that state an answer
CONCLUSION_RX = re.compile(r"(?i)(the (final )?answer is|final answer|so the answer|\\boxed|"
                           r"(so|therefore|thus),? (each|the total|the result)[^.]* (is|pays|are|=) ?\d)")
# Validation on Enter (2026-11-14): the notes are a speculation on a message that was not finished; instead of
# making them less wrong, check them and answer from scratch when the check fails.
# VERIFY_PROBE: one token asked of the model itself, after the notes and before SENT; P(yes) under
# VERIFY_THRESHOLD drops the notes (the request goes through as it is, as after a loop).
VERIFY_PROBE = False
VERIFY_THRESHOLD = 0.5
VERIFY_TEXT = ("\n\nCheck: the material above was written while the message was being typed. Is every conclusion "
               "in it still right for the complete message? Answer yes or no: ")
VERIFY_N_PROBS = 20          # top tokens asked for; yes/no mass is read among them
# BRANCHES: k independent reasonings ahead, one per engine slot, sampled with different seeds; on Enter the k
# answers from their notes are compared (the final number, else the whole answer): unanimous -> that answer,
# otherwise the request goes through from scratch. 1 = off.
BRANCHES = 1
RECORD_TURNS = False         # keep the notes' text and the checks of the last turn in ProxyState.last_turn (bench)
NUMBER_RX = re.compile(r"-?\d+(?:[.,]\d+)?")
# A time is compared whole: two branches at 21:55 and 22:55 "agreed" on 55 (2026-11-14, held-out problem 27)
ANSWER_RX = re.compile(r"\d{1,2}:\d{2}|-?\d+(?:[.,]\d+)?")
LANG_WAIT_WORDS = 8         # the reasoning waits for the message's language up to this many words
# A branch that looped stayed without notes for the rest of the turn, and the turn went from scratch (11 of 40
# held-out turns with BRANCHES=2, 2026-11-14): with LOOP_RESTART it starts over instead, LOOP_RESTARTS_MAX times.
LOOP_RESTART = True          # only with BRANCHES > 1: a restarted branch is still checked against the other
LOOP_RESTARTS_MAX = 2
LOOP_REPEATS = 3
LOOP_MIN_CHARS = 8
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
                 "repeat_penalty", "seed")


class Frame:
    """The frame's token ids for this engine's tokenizer, computed once."""

    def __init__(self, engine):
        tok = engine.tokenize
        self.seed = tok(SEED) or []
        self.seed_en = tok(SEED_EN) or []          # a frame of its own (the agent's) sets this to []
        self.sent = tok(SENT_REDERIVE if SENT_MODE == "rederive" else SENT) or []
        self.bridge_edit = tok(BRIDGE_EDIT) or []
        end = tok("</think>") or []
        self.think_end = end[0] if len(end) == 1 else None
        meta = set()
        for w in META_WORDS:
            for v in (w, " " + w):
                ids = tok(v) or []
                if len(ids) == 1:
                    meta.add(ids[0])
        self.meta_bias = [[t, META_BIAS] for t in sorted(meta)] if META_BIAS else []
        answer = set()
        for w in ANSWER_WORDS:
            ids = tok(w) or []
            if len(ids) == 1:
                answer.add(ids[0])
        self.answer_ids = sorted(answer)
        self.tokenize = tok
        # a frame of its own may turn these on for itself (the comprehension branch), whatever the globals say
        self.end_ban = False
        self.stop_bridges = False

    def bias(self) -> list:
        """The logit bias of a chunk written ahead, with the switches above as they are now."""
        out = list(self.meta_bias)
        if ANSWER_BIAS:
            out += [[t, ANSWER_BIAS] for t in self.answer_ids]
        if (END_BAN or self.end_ban) and self.think_end is not None:
            out.append([self.think_end, -100.0])
        return out

    @property
    def usable(self) -> bool:
        return bool(self.seed and self.sent and self.think_end is not None)

    def seed_for(self, text: str) -> list[int]:
        """The frame's opening for this message: the English example line for an English one."""
        return self.seed_en if self.seed_en and english(text) else self.seed

    def seed_len(self, notes: list[int]) -> int:
        """How long the opening of these notes is (either variant)."""
        for seed in (self.seed, self.seed_en):
            if seed and notes[:len(seed)] == seed:
                return len(seed)
        return len(self.seed)


def language(text: str) -> str | None:
    """"en" or "it" by the words above, None while it cannot tell (no such word yet, or as many of each)."""
    words = [w.strip(".,;:?!\"'()").lower() for w in text.split()]
    en, it = sum(w in EN_WORDS for w in words), sum(w in IT_WORDS for w in words)
    return "en" if en > it else "it" if it > en else None


def question_started(text: str) -> bool:
    """Has the question begun after some setup? True when the last sentence (maybe unfinished) opens with a
    question word, or the text ends with "?", and at least one sentence came before it. A message that opens
    with its question is reasoned on as before."""
    parts = [p for p in SENTENCE_RX.split(text.strip()) if p.strip()]
    if len(parts) < 2:
        return False
    last = parts[-1].strip().lower()
    first = last.split()[0].strip(",;:") if last.split() else ""
    return first in QUESTION_WORDS or text.rstrip().endswith("?")


def english(text: str) -> bool:
    return language(text) == "en"


def looping(text: str) -> bool:
    """Does the model's own reasoning (bridges left out) repeat one line LOOP_REPEATS times?"""
    seen: dict[str, int] = {}
    for line in text.splitlines():
        key = " ".join(line.split()).strip(" -*:.").lower()
        if len(key) < LOOP_MIN_CHARS:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= LOOP_REPEATS:
            return True
    return False


def thinking_on(template_kwargs: dict | None) -> bool:
    return (template_kwargs or {}).get("enable_thinking", True) is not False


class Thinker:
    """One conversation's reasoning ahead: the notes after the draft, grown chunk by chunk."""

    def __init__(self, session, frame: Frame, enabled=lambda: True):
        self.session, self.frame, self.enabled = session, frame, enabled
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopped = False
        self.dropped = 0                       # turns whose notes were dropped for looping
        self.conclusions_dropped = 0           # lines stating an answer taken out on Enter (DROP_CONCLUSIONS)
        self.restarts = 0                      # this turn's restarts after a loop (LOOP_RESTART)
        self.verified_dropped = 0              # turns whose notes the check on Enter dropped (VERIFY_PROBE)
        self.last_verify: float | None = None  # P(yes) of the last check
        self._clear()
        threading.Thread(target=self._loop, daemon=True).start()

    def _clear(self):
        self.notes: list[int] = []             # SEED, reasoning and bridges, after the draft's prompt
        self.notes_text = ""                   # the draft the notes last followed
        self.bridge_text = ""                  # up to where the bridges named the new words
        self.fresh = 0                         # reasoning tokens since the last bridge
        self.finished = False                  # the model closed its reasoning (</think>)
        self.written = ""                      # the model's own reasoning as text, a line break per bridge
        self.looped = False                    # it went round in circles: stop, and drop the notes on Enter

    def reset(self):
        """A new turn: no notes."""
        with self.lock:
            self._clear()
            self.restarts = 0

    @property
    def active(self) -> bool:
        """Reasoning on this draft (then plain read-ahead would only evict the notes from the cache)."""
        # after a loop the notes are dropped on Enter: plain read-ahead goes on with the rest of the message
        return self.enabled() and not self.looped and len(self.session.latest_text.split()) >= THINK_MIN_WORDS

    def poke(self):
        self.wake.set()

    @property
    def reasoned(self) -> int:
        return max(0, len(self.notes) - self.frame.seed_len(self.notes)) if self.notes else 0

    # ---- while the user types -----------------------------------------------------------
    def _loop(self):
        while not self.stopped:
            self.wake.wait(0.25)
            self.wake.clear()
            try:
                while self._step():
                    pass
            except Exception:                  # thinking ahead is best-effort, never a crash
                time.sleep(0.5)

    def _new_words(self, text: str) -> tuple[str, int]:
        start = len(os.path.commonprefix([self.bridge_text, text]))
        new = text[start:]
        if new[-1:].isalnum():                 # the last word may still be being typed
            new = new[:max(new.rfind(" "), 0)]
        return new.strip(), start + len(new)

    def _bridge(self, text: str, force: bool = False) -> list[int]:
        new, end = self._new_words(text)
        if new and len(new.split()) < BRIDGE_MIN_WORDS and not force:
            return []
        ids = self.frame.tokenize(BRIDGE.format(new=new[-BRIDGE_NEW_CHARS:])) if new else self.frame.bridge_edit
        self.bridge_text = text[:end] if new else text
        return ids or []

    def _step(self) -> bool:
        """Write one chunk if there is something to do.  True when it wrote one."""
        s = self.session
        if self.stopped or not self.enabled() or s.cache.generating:
            return False
        with s.lock:
            prompt, text = list(s.latest), s.latest_text
        if not prompt or len(text.split()) < THINK_MIN_WORDS:
            return False
        with self.lock:
            if self.notes and len(os.path.commonprefix([text, self.notes_text])) < len(self.notes_text) // 2:
                self._clear()                  # the message was mostly rewritten: old notes would mislead
            if (not self.notes and self.frame.seed_en and language(text) is None
                    and len(text.split()) < LANG_WAIT_WORDS):
                return False                   # which example line? wait for a word that tells the language
            if not self.notes:
                self.notes, self.notes_text, self.bridge_text = list(self.frame.seed_for(text)), text, ""
            elif text != self.notes_text and (self.finished or self.fresh >= BRIDGE_EVERY):
                bridge = self._bridge(text, force=self.finished and FORCE_BRIDGE)
                if bridge:
                    self.notes += bridge
                    self.written += "\n"
                    self.fresh, self.finished = 0, False
            if self.looped:
                return False                   # going round in circles: no more reasoning this turn
            if QUESTION_STOP and question_started(text):
                return False                   # the question is being typed: it is read whole after Enter
            if self.finished and text == self.notes_text:
                return False                   # done reasoning on this text: wait for more typing
            if self.reasoned >= THINK_CAP:
                return False
            ids = prompt + self.notes
        if not s.cache.warm_lock.acquire(timeout=0.05):
            return False
        try:
            if s.cache.generating or self.stopped:
                return False
            stops = STOP_BRIDGES or self.frame.stop_bridges
            sampling = dict(THINK_SAMPLING, stop=list(BRIDGE_STOPS)) if stops else THINK_SAMPLING
            if BRANCHES > 1:                   # branches must not write the same notes: a seed of their own
                sampling = dict(sampling, seed=random.randrange(1, 2 ** 31))
            res = s.engine.generate(ids, n_predict=THINK_CHUNK, sampling=sampling,
                                    logit_bias=self.frame.bias(), abort=s.cache.abort)
            s.cache.set(ids + list(res.get("tokens") or []))
        finally:
            s.cache.warm_lock.release()
        tokens = list(res.get("tokens") or [])
        if (STOP_BRIDGES or self.frame.stop_bridges) and res.get("stop_type") == "word":
            # stopped at a bridge line of the model's own, or at the template's markers: keep the text before
            # it, as tokens, and wait for more typing as when the reasoning ends
            kept = str(res.get("text") or "")
            tokens = list(self.frame.tokenize(kept) or []) if kept.strip() else []
            with self.lock:
                self.finished = True
        if not tokens:
            return False
        with self.lock:
            if self.notes_text and ids[len(prompt):] != self.notes:
                return False                   # reset or bridged meanwhile: the chunk is stale
            end = self.frame.think_end
            words = str(res.get("text") or "")
            if end in tokens:
                tokens = tokens[:tokens.index(end)]
                words = words.split("</think>", 1)[0]
                self.finished = True
            self.notes += tokens
            self.written += words
            if looping(self.written):
                if LOOP_RESTART and BRANCHES > 1 and self.restarts < LOOP_RESTARTS_MAX:
                    self.restarts += 1         # start over on the text as it is now, instead of staying dead
                    self._clear()
                    return True
                self.looped = True
            self.fresh += len(tokens)
            self.notes_text = text
        return True

    # ---- on Enter -------------------------------------------------------------------------
    def take(self, final_text: str) -> list[int] | None:
        """The notes for the message as sent, ready to follow its prompt (bridge and SENT included),
        or None if nothing was reasoned yet (then the request goes through as it is)."""
        with self.lock:
            if self.reasoned < 1:
                return None
            if self.looped:
                self.dropped += 1
                return None                    # the request goes through as it is, from scratch
            notes = list(self.notes)
            if DROP_CONCLUSIONS:
                notes = self._without_conclusions(notes)
            if final_text.rstrip() != self.notes_text.rstrip():
                saved = self.bridge_text
                notes += self._bridge(final_text + " ", force=True)
                self.bridge_text = saved
            return notes + self.frame.sent

    def _without_conclusions(self, notes: list[int]) -> list[int]:
        """The notes with every line that states an answer taken out (written before the question was
        complete, such a line was kept after Enter in 3 of the 4 answers lost): the material stays, the
        verdict goes. The notes are tokenized again from the first line removed (a prefill on Enter)."""
        start = self.frame.seed_len(notes)
        detok = getattr(self.session.engine, "detokenize", None)
        if detok is None:
            return notes
        text = detok(notes[start:])
        lines = text.split("\n")
        kept = [ln for ln in lines if not CONCLUSION_RX.search(ln)]
        if len(kept) == len(lines):
            return notes
        self.conclusions_dropped += len(lines) - len(kept)
        return notes[:start] + list(self.frame.tokenize("\n".join(kept)) or [])


# ---- validation on Enter ---------------------------------------------------------------------
def notes_core(notes: list[int], frame: Frame) -> list[int]:
    """The notes as take() returns them, without SENT at the end."""
    n = len(frame.sent)
    return notes[:len(notes) - n] if n and notes[-n:] == frame.sent else list(notes)


def verify(engine, prompt: list[int], notes: list[int], frame: Frame) -> float | None:
    """P(yes) that the notes still hold for the complete message, asked of the model in one token after the
    notes (the final bridge included) and VERIFY_TEXT; None when the engine cannot tell (no probabilities).
    A request of its own on the conversation's slot: its prompt shares prompt + notes with the answer's, so
    the answer that follows reuses that prefix and never sees the probe."""
    probs = getattr(engine, "next_token_probs", None)
    if probs is None:
        return None
    probe = frame.tokenize(VERIFY_TEXT) or []
    try:
        top = probs(prompt + notes_core(notes, frame) + probe, n_probs=VERIFY_N_PROBS)
    except Exception:
        return None
    yes = sum(p for t, p in top.items() if t.strip().lower() == "yes")
    no = sum(p for t, p in top.items() if t.strip().lower() == "no")
    return yes / (yes + no) if yes + no > 0 else None


def final_answer(text: str) -> str:
    """What two answers are compared on: the last number (commas and a trailing .0 left out), else the
    whole answer, lowercased, spaces collapsed."""
    found = ANSWER_RX.findall(text or "")
    if found:
        n = found[-1].replace(",", "")
        return n[:-2] if n.endswith(".0") else n
    return " ".join((text or "").lower().split())


def answer_text(chunks: list[dict]) -> str:
    return "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks if c.get("choices"))


class _BranchCache:
    """A branch's view of the shared cache: a lock of its own (its chunks run on its own slot, in parallel
    with the conversation's), while Enter (abort, generating) is the conversation's."""

    def __init__(self, main):
        self.main = main
        self.warm_lock = threading.Lock()

    @property
    def generating(self) -> bool:
        return self.main.generating

    @property
    def abort(self):
        return self.main.abort

    def set(self, ids, **kw):
        pass                                   # the conversation's slot is untouched


class BranchView:
    """What a branch's Thinker needs of the session: the same draft, its own engine slot and lock."""

    def __init__(self, sess, engine):
        self._sess = sess
        self.engine = engine
        self.cache = _BranchCache(sess.cache)

    @property
    def lock(self):
        return self._sess.lock

    @property
    def latest(self):
        return self._sess.latest

    @property
    def latest_text(self):
        return self._sess.latest_text


def branch_engine(engine, slot: int):
    """The same engine on another slot (llama-server's id_slot)."""
    import copy
    e = copy.copy(engine)
    e.slot = slot
    return e


# The other branches' frame. "same": the conversation's. "comprehend": they only read while the user types,
# a line of facts per sentence, no computing, no solving, no guessing the question (as StreamingThinker's
# comprehension phase, Tong et al. 2025, arXiv:2510.17238), and solve after Enter: less correlated with the
# conversation's branch, whose notes commit to a reading of the problem (the tip, 2026-11-14).
BRANCH_FRAME = "same"
COMPREHEND_SEED = ("<think>\nThe user is still typing. While I wait I only read: for each sentence, one line with "
                   "the quantities in it and what they belong to. I do not compute, do not solve and do not guess "
                   "the question; that comes when the message is complete.\n\n- ")
COMPREHEND_SENT = ("\n\nThe user has now sent the complete message. Now let me work out exactly what it asks and "
                   "solve it step by step, using the facts above.\n\n")


def comprehend_frame(engine) -> Frame:
    """The frame of a branch that reads without solving (BRANCH_FRAME = "comprehend")."""
    f = Frame(engine)
    f.seed = engine.tokenize(COMPREHEND_SEED) or f.seed
    f.seed_en = []
    f.sent = engine.tokenize(COMPREHEND_SENT) or f.sent
    # told that solving comes later, the 4B closed its reasoning at once and the branch had no notes in 13
    # of 36 turns (2026-11-14): it may not close it, and stops at a bridge or a template marker of its own
    f.end_ban = True
    f.stop_bridges = True
    return f


def run_branches(jobs: list[tuple], frame: Frame, body: dict) -> list[list[dict]]:
    """Each (engine, prompt, notes[, frame]) answered at once on its own slot; the chunks of each, in order.
    Buffered: the caller compares the answers before any is sent."""
    out: list[list[dict]] = [[] for _ in jobs]

    def one(i, engine, prompt, notes, fr=None):
        try:
            out[i] = list(answer(engine, prompt, notes, fr or frame, body))
        except Exception:
            out[i] = []
    threads = [threading.Thread(target=one, args=(i, *job), daemon=True) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _sampling(body: dict) -> dict:
    out = dict(THINK_SAMPLING)
    out.update({k: body[k] for k in SAMPLING_KEYS if body.get(k) is not None})
    return out


class ToolCalls:
    """Qwen3 writes a tool call as <tool_call>\\n{"name": ..., "arguments": {...}}\\n</tool_call>:
    split the answer's text into plain content and OpenAI tool calls."""

    OPEN, CLOSE = "<tool_call>", "</tool_call>"

    def __init__(self):
        self.buf = ""
        self.calls: list[dict] = []

    def feed(self, text: str) -> tuple[str, list[dict]]:
        """Content safe to show now, and tool calls completed by this text."""
        self.buf += text
        out, done = "", []
        while True:
            i = self.buf.find(self.OPEN)
            if i < 0:
                # keep a tail that could be the start of "<tool_call>"
                keep = max((k for k in range(1, len(self.OPEN)) if self.buf.endswith(self.OPEN[:k])), default=0)
                out += self.buf[:len(self.buf) - keep]
                self.buf = self.buf[len(self.buf) - keep:]
                return out, done
            out += self.buf[:i]
            j = self.buf.find(self.CLOSE, i)
            if j < 0:
                self.buf = self.buf[i:]
                return out, done
            raw = self.buf[i + len(self.OPEN):j].strip()
            self.buf = self.buf[j + len(self.CLOSE):]
            try:
                call = json.loads(raw)
                args = call.get("arguments", {})
                done.append({"index": len(self.calls), "id": "call_" + uuid.uuid4().hex[:24], "type": "function",
                             "function": {"name": call.get("name", ""),
                                          "arguments": args if isinstance(args, str) else json.dumps(args)}})
                self.calls.append(done[-1])
            except (ValueError, AttributeError):
                out += self.OPEN + raw + self.CLOSE

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return out


def answer(engine, prompt: list[int], notes: list[int], frame: Frame, body: dict) -> Iterable[dict]:
    """The answer after the notes, as OpenAI chat-completion chunks (dicts), the last with
    finish_reason, usage and llama-server's timings.  The notes are shown first as reasoning."""
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model = body.get("model", "")

    def chunk(delta: dict, finish: str | None = None, **extra) -> dict:
        return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}

    yield chunk({"role": "assistant", "content": None})
    seen = engine.detokenize(notes[frame.seed_len(notes):len(notes) - len(frame.sent)])
    if seen.strip():
        yield chunk({"reasoning_content": "- " + seen.lstrip()})
    limit = body.get("max_completion_tokens") or body.get("max_tokens") or -1
    thinking, tools, produced, final = True, ToolCalls(), 0, {}
    pending = ""
    for ev in engine.stream(prompt + notes, n_predict=limit, sampling=_sampling(body)):
        if ev.get("error"):
            final = {"stop_type": "error"}
            break
        produced += len(ev.get("tokens") or [])
        text = ev.get("text", "")
        if thinking:
            pending += text
            if "</think>" in pending:
                before, after = pending.split("</think>", 1)
                if before:
                    yield chunk({"reasoning_content": before})
                thinking, pending = False, ""
                text = after.lstrip("\n")
            else:
                safe = len(pending) - max((k for k in range(1, 8) if pending.endswith("</think>"[:k])), default=0)
                if safe > 0:
                    yield chunk({"reasoning_content": pending[:safe]})
                    pending = pending[safe:]
                text = ""
        if not thinking and text:
            content, calls = tools.feed(text)
            if content:
                yield chunk({"content": content})
            for call in calls:
                yield chunk({"tool_calls": [call]})
        if ev.get("stop"):
            final = ev
            break
    rest = tools.flush() if not thinking else pending
    if rest:
        yield chunk({"content": rest} if not thinking else {"reasoning_content": rest})
    finish = "tool_calls" if tools.calls else ("length" if final.get("stop_type") == "limit" else "stop")
    t = final.get("timings") or {}
    yield chunk({}, finish, usage={"prompt_tokens": len(prompt) + len(notes), "completion_tokens": produced,
                                   "total_tokens": len(prompt) + len(notes) + produced},
                timings=t)


def collect(chunks: Iterable[dict]) -> dict:
    """A non-streamed chat completion from the chunks."""
    content, reasoning, calls, last = "", "", [], {}
    for c in chunks:
        d = c["choices"][0]["delta"]
        content += d.get("content") or ""
        reasoning += d.get("reasoning_content") or ""
        calls += d.get("tool_calls") or []
        last = c
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if calls:
        msg["tool_calls"] = [{k: v for k, v in c.items() if k != "index"} for c in calls]
    return {"id": last.get("id"), "object": "chat.completion", "created": last.get("created"), "model": last.get("model"),
            "choices": [{"index": 0, "message": msg, "finish_reason": last["choices"][0]["finish_reason"]}],
            "usage": last.get("usage"), "timings": last.get("timings")}
