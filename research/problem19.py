"""Problem 19 on a slow model: where do the wrong numbers come from? (plan item A1, LOG "Next" 0)

On the RTX 2060's Qwen3-14B the problem "30 vehicles, 100 wheels, how many motorbikes" (answer 10) was wrong
with reasoning ahead in 2 of 2 runs ("38 × 2 + 2 × 4 = 84", "x + y = 60") and right without it in 2 of 2.
thinking_ahead.py keeps only the answer's tail; this script keeps everything: the notes written ahead (the
reasoning on the unfinished message, as they stand on Enter), the whole reasoning and answer after Enter,
and the drafts the notes were extended on. Same typist and the same problem text as thinking_ahead.py.

    python research/problem19.py research/data/problem19_14b_2060.json --gguf <14B GGUF> --runs 3

Each run is a fresh chat; the model is loaded once per config (the 14B takes a minute), "reason ahead"
first; rerunning the command goes on where it stopped.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thinking_ahead import PROBLEMS, correct, type_message, wait  # noqa: E402

from mafdet.cli import build_parser  # noqa: E402
from mafdet.watch import Session  # noqa: E402

CONFIGS = {"reason ahead": [], "no ahead": ["--ahead", "0"]}


def run(s: Session, msg: str, expect: list[str], seed: int) -> dict:
    s.new_chat()
    drafts: list[str] = []
    orig = s.draft

    def draft(text):
        drafts.append(text)
        return orig(text)
    s.draft = draft
    try:
        type_message(s, msg, random.Random(seed))
    finally:
        s.draft = orig
    notes = s.tok.decode(s.notes) if s.notes else ""
    notes_text = s.notes_text
    events, answer, done = [], "", None
    for ev in s.send(msg):
        if ev["type"] == "token":
            answer += ev["text"]
        elif ev["type"] == "done":
            done = ev
        else:
            events.append({k: v for k, v in ev.items() if k != "text"})
    return {"ok": correct(answer, expect), "notes_on_enter": notes, "notes_extended_on": notes_text,
            "answer": answer, "done": {k: v for k, v in (done or {}).items() if isinstance(v, (int, float, str, bool))},
            "drafts": len(drafts)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--gguf", default="")
    ap.add_argument("--runs", type=int, default=3, help="runs per config")
    ap.add_argument("--max-tokens", default="1800")
    ap.add_argument("--problem", type=int, default=19)
    a = ap.parse_args()
    msg, expect = PROBLEMS[a.problem]
    out = Path(a.out)
    rows = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    for name, extra in CONFIGS.items():
        done = sum(1 for r in rows if r["config"] == name)
        if done >= a.runs:
            continue
        model = ["--gguf", a.gguf] if a.gguf else []
        s = Session(build_parser().parse_args(["strike", "--max-tokens", a.max_tokens] + model + extra))
        wait(lambda: s.state != "loading")
        assert s.state == "ready", s.error
        s.draft("Ciao")                       # warm-up, as the 14B runs of thinking_ahead.py
        list(s.send("Ciao"))
        for i in range(done, a.runs):
            t0 = time.time()
            r = run(s, msg, expect, seed=a.problem)
            r.update(config=name, run=i, problem=a.problem, seconds=round(time.time() - t0, 1))
            rows.append(r)
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name:12s} run {i}: {'OK ' if r['ok'] else 'BAD'} {r['seconds']} s, notes {len(r['notes_on_enter'])} "
                  f"chars, answer tail: {r['answer'][-80:]!r}", flush=True)
        s.close()
    for name in CONFIGS:
        rs = [r for r in rows if r["config"] == name]
        print(f"{name}: {sum(r['ok'] for r in rs)}/{len(rs)} right")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
