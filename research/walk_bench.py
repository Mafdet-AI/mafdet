"""The walk's ruler: `mafdet agent --walk` run on binaries whose real function names are known (public
symbols, `walk_bench_keys.py`), its given names scored against the real ones, so two versions of the walk
can be compared with numbers instead of a reading.

    python research/walk_bench.py run baseline                       # the set, the walk's defaults
    python research/walk_bench.py run nothink --walk-args="--no-think"
    python research/walk_bench.py run noise --only where --repeat 2
    python research/walk_bench.py score research/data/walk_bench_baseline.json   # rescore, no GPU
    python research/walk_bench.py book <book.json> <key.json> [--judge]          # one book against a key
    python research/walk_bench.py calibrate <result.json>... --labels research/data/walk_bench_labels.json
    python research/walk_bench.py compare research/data/walk_bench_baseline.json research/data/walk_bench_nothink.json
    python research/walk_bench.py second revise --work E:/mafdet-tools/bench-runs/revise        # pass 2 on copies
    python research/walk_bench.py second refine --pass-args="--refine" --tree <upstream tree> --work ...
    python research/walk_bench.py compare research/data/walk_bench_revise.json research/data/walk_bench_refine.json

`second` (alias `revise`) runs a second pass without paying the first again: each finished run's home
(--from <work>/<binary>-r1/home: its book and mcp.json) is copied to a fresh folder and the walk run there with
--pass-args (default --revise; --refine for upstream's) from --tree (cwd and PYTHONPATH: that tree's mafdet is
the one imported, recorded with its commit). The first pass is done in the copy, so the walk sends none of its
requests: checked per run (`first_pass_check`: the walk's `to do 0`, no first name or first-pass cost changed).
Each book then scores as two results: pass 1 (`first.name` where the second pass kept the first reading, else
`name`) and pass 2 (`name`), read from the JSON (both the local `revision` and upstream's `refine` books), with
pass 2 minus pass 1 paired per function; the judge's baseline answers are reused, and an unchanged name gets
its pass-1 answer in pass 2 (only new names are judged).

`run` starts llama-server once (or uses --url) and, for each binary of the set file
(research/data/walk_bench_set.json), runs the walk as a user would (`python -m mafdet agent --walk <binary>
--mcp rea --url <engine>`) in a fresh MAFDET_HOME of its own (a copy of mcp.json, nothing else: the books
of two runs never mix, the user's ~/.mafdet/binaries is never touched); rea is read-only on Windows, so
every run starts from the decompiler's own names. Then each named function is scored, the judge asked (the
same engine, thinking off), and everything written to research/data/walk_bench_<label>.json; a table is
printed. The binaries are walked WITHOUT their symbols: the PDBs stay in their own folder, away from the
binaries, and only this script reads them (through the keys).

The score (the pure functions below; tests in tests/test_walk_bench.py):
  - token F1, the measure of the function-name-recovery literature: both names split into lowercase word
    tokens (snake_case, camelCase, acronyms, digits dropped, A/W/Ex suffixes dropped, a few stop words), a
    short table of abbreviations and strict synonyms (NORMAL), plurals folded; precision = shared / given,
    recall = shared / real, F1 their harmonic mean. Exact, cheap, and blunt: `wmain` against
    `parse_options_and_search_path` is 0 whatever the quality.
  - the judge: the model, thinking off, temperature 0, told the program, the real name, the given name and
    its summary, answers same / related / different (JUDGE_PROMPT). Sees meaning past the tokens; it is the
    same model that named the function, and it reads names only, not code.
  - by layer: height in the call graph of the functions the walk names (a leaf calls none of them; a
    function's height is one more than its highest callee's, a cycle counted once): leaf (0), top (at least
    half the binary's greatest height, and at least 2), middle (the rest).
  - by kind: runtime (C runtime, compiler support, the SDK's inline helpers: RUNTIME) against the program's
    own functions; nobody needs the model to name the CRT.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
DEFAULT_SET = DATA / "walk_bench_set.json"

# ---------------------------------------------------------------- tokens and F1

# Abbreviation or strict synonym -> one canonical token. Short on purpose: abbreviations of the same word,
# and the get/fetch kind of pair the task names; nothing chosen by looking at what the model writes.
NORMAL = {
    "str": "string", "wstr": "string", "wstring": "string", "wcs": "string", "strings": "string",
    "alloc": "allocate", "allocation": "allocate", "allocator": "allocate", "realloc": "reallocate",
    "init": "initialize", "initialise": "initialize", "initialization": "initialize", "initialisation": "initialize",
    "msg": "message", "buf": "buffer", "fetch": "get", "retrieve": "get",
    "len": "length", "num": "number", "cnt": "count", "dir": "directory", "cmd": "command",
    "arg": "argument", "args": "argument", "param": "parameter", "params": "parameter",
    "opt": "option", "err": "error", "mem": "memory", "info": "information", "ctx": "context",
    "cfg": "config", "configuration": "config", "res": "resource", "attr": "attribute", "val": "value",
    "src": "source", "dst": "destination", "dest": "destination", "max": "maximum", "min": "minimum",
    "char": "character", "chr": "character", "ch": "character", "env": "environment", "proc": "procedure",
    "addr": "address", "ptr": "pointer", "idx": "index", "cmp": "compare", "calc": "calculate",
    "dealloc": "deallocate", "util": "utility", "wmain": "main", "tmain": "main",
    "destructor": "destroy", "dtor": "destroy", "destruct": "destroy", "constructor": "construct", "ctor": "construct",
}
STOP = {"a", "an", "the", "of", "to", "and", "or", "for", "by", "with", "from", "in", "on", "into", "at"}
# Windows' charset and variant suffixes: StringCopyW, ShowMessageEx, FindString2.
SUFFIX_NOISE = {"w", "a", "ex"}
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokens(name: str) -> list[str]:
    """A function name -> its words: lowercase, normalized, in order, each once.
    `?Search@CWhere@@...`-style decorations are undecorated by the keys; here only the C identifier is
    split (a `Class::method` keeps the method)."""
    name = (name or "").rsplit("::", 1)[-1]
    name = re.sub(r"\$.*$", "", name)                # compiler suffixes: foo$filt$0, _thunk$...
    out: list[str] = []
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    for i, part in enumerate(parts):
        words = _CAMEL.findall(part)
        for j, w in enumerate(words):
            lw = w.lower()
            if lw.isdigit():
                continue
            last = i == len(parts) - 1 and j == len(words) - 1
            if lw in SUFFIX_NOISE and (last or len(lw) == 1) and (len(words) > 1 or len(parts) > 1):
                continue
            if lw in STOP:
                continue
            lw = NORMAL.get(lw, lw)
            lw = NORMAL.get(_stem(lw), _stem(lw))
            if lw and lw not in out:
                out.append(lw)
    return out


def prf(given: str, real: str) -> tuple[float, float, float]:
    """Token precision, recall and F1 of a given name against the real one (sets of tokens)."""
    g, r = set(tokens(given)), set(tokens(real))
    if not g or not r:
        return 0.0, 0.0, 0.0
    hit = len(g & r)
    if not hit:
        return 0.0, 0.0, 0.0
    p, rc = hit / len(g), hit / len(r)
    return p, rc, 2 * p * rc / (p + rc)


def f1_bucket(f1: float) -> str:
    """For the cross table with the judge and the hand labels: high (>= 0.5), some (> 0), none."""
    return "high" if f1 >= 0.5 else "some" if f1 > 0 else "none"


# ---------------------------------------------------------------- runtime or program

RUNTIME = re.compile(
    r"^(?:"
    r"_*(?:w?main|w?WinMain|DllMain)CRTStartup|__scrt_\w+|__security_\w+|__report_\w+|__raise_\w+|_+guard_\w+"
    r"|_RTC_\w+|_CRT_\w+|__CRT\w*|__GSHandler\w*|__C_specific_handler\w*|__Cxx\w+|_CxxThrow\w*|_XcptFilter"
    r"|_ValidateImageBase|_FindPESection|_IsNonwritableInCurrentImage|RtlpImageNtHeader|pre_c?p?p?_init"
    r"|_*initterm\w*|__acrt_\w+|__vcrt_\w+|__std_\w+|_get_image_app_type|_w?setargv|_w?setenvp|_matherr"
    r"|__delayLoadHelper\w*|__HrLoadAllImportsForDll|_*tailMerge\w*|__dyn_tls_\w+|__tlregdtor|_onexit|atexit"
    r"|String(?:C[bc]|V)\w*|StringCopyWorker\w*|StringLengthWorker\w*|StringExValidate\w*"
    r"|_?_?(?:tlg|Tlg|TraceLogging)\w*|MicrosoftTelemetryAssert\w*|ApiSetQueryApiSetPresence\w*"
    r"|_Init_thread_\w+|operator_new|operator_delete|\w*_deleting_destructor"     # C++ support, compiler-made
    r"|Is\w+Present|load_\w+|DelayLoad_\w+"           # api-set presence checks and delay-load stubs (generated)
    r"|_+[a-z][a-z0-9]*(?:_[a-z0-9]+)*"               # _wsetargv, __iob_func: lowercase after the underscores
    r")$")


def score_name(short: str, full: str | None) -> str:
    """The real name the tokens are taken from: the function's own part (`XCOPY::DoCopy` -> DoCopy, as in the
    literature), but a constructor or destructor keeps its class (`PAGER::destructor` -> PAGER_destructor):
    alone the word says nothing of what it destroys."""
    if short in ("constructor", "destructor") and full and "::" in full:
        return full.replace("::", "_")
    return short


def is_runtime(real: str) -> bool:
    """A real name from the C runtime, compiler support or the SDK's inline helpers (strsafe, TraceLogging),
    not from the program. `__DynArrayAppend` (an internal library of Microsoft's command-line tools) is the
    program's: the rule for leading underscores asks for a lowercase word after them."""
    return bool(RUNTIME.match((real or "").rsplit("::", 1)[-1]))


# ---------------------------------------------------------------- layers

def heights(functions: dict) -> dict[str, int]:
    """Height of each function the walk names (not `known`) in the call graph restricted to them: 0 for a
    leaf, else 1 + the highest callee; a strongly connected component shares one height. Iterative."""
    nodes = [a for a, f in functions.items() if f.get("status") != "known"]
    inside = set(nodes)
    edges = {a: [c for c in (functions[a].get("callees") or []) if c in inside and c != a] for a in nodes}
    # Tarjan, iterative: components come out callees first.
    index, low, on, stack, comp_of, comps = {}, {}, set(), [], {}, []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        work = [(root, iter(edges[root]))]
        while work:
            v, it = work[-1]
            pushed = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(edges[w])))
                    pushed = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    comp_of[w] = len(comps)
                    if w == v:
                        break
                comps.append(comp)
    h_comp: list[int] = []
    for i, comp in enumerate(comps):                  # callees' components were finished before
        below = [h_comp[comp_of[c]] for a in comp for c in edges[a] if comp_of[c] != i]
        h_comp.append(1 + max(below) if below else 0)
    return {a: h_comp[comp_of[a]] for a in nodes}


def layer_of(height: int, top_height: int) -> str:
    if height == 0:
        return "leaf"
    if height >= max(2, math.ceil(top_height / 2)):
        return "top"
    return "middle"


# ---------------------------------------------------------------- scoring a book

def score_book(book: dict, key: dict) -> list[dict]:
    """One row per function the walk was to name: address, real name (None when the key has none), given
    name, summary, layer, kind, P/R/F1, status, seconds, tokens."""
    fs = book["functions"]
    kf = key["functions"]
    hs = heights(fs)
    top = max(hs.values(), default=0)
    rows = []
    for a in sorted(hs, key=lambda x: int(x, 16) if x.startswith("0x") else 0):
        f = fs[a]
        k = kf.get(a)
        real = k["name"] if k else None
        given = f.get("name") or ""
        scored = score_name(real, k["full"]) if k else None
        p, r, f1 = prf(given, scored) if (real and f.get("status") == "done") else (0.0, 0.0, 0.0)
        rows.append({
            "address": a, "real": real, "real_full": k["full"] if k else None, "given": given,
            "summary": f.get("summary") or "", "confidence": f.get("confidence") or "", "status": f.get("status"),
            "height": hs[a], "layer": layer_of(hs[a], top), "kind": ("runtime" if is_runtime(real) else "program")
            if real else None, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "real_tokens": tokens(scored) if real else [], "given_tokens": tokens(given),
            "seconds": f.get("seconds") or 0.0, "prompt_tokens": f.get("prompt_tokens") or 0,
            "completion_tokens": f.get("completion_tokens") or 0, "steps": f.get("steps") or 0,
            "judge": None})
    return rows


JUDGE_SCORE = {"same": 1.0, "related": 0.5, "different": 0.0}


def aggregate(rows: list[dict]) -> dict:
    """Means over the scored rows (a real name in the key and a given name): F1, P, R, the judge's mix and
    its score (same 1, related 0.5, different 0); counts; seconds and tokens over every function walked."""
    scored = [r for r in rows if r["real"] and r["status"] == "done"]
    walked = [r for r in rows if r["status"] in ("done", "failed")]
    out = {"functions": len(rows), "walked": len(walked), "named": sum(1 for r in rows if r["status"] == "done"),
           "failed": sum(1 for r in rows if r["status"] == "failed"), "with_key": sum(1 for r in rows if r["real"]),
           "scored": len(scored)}
    if scored:
        for k in ("f1", "precision", "recall"):
            out[k] = round(statistics.mean(r[k] for r in scored), 4)
        out["f1_zero"] = round(sum(1 for r in scored if r["f1"] == 0) / len(scored), 4)
        out["f1_buckets"] = {b: sum(1 for r in scored if f1_bucket(r["f1"]) == b) for b in ("high", "some", "none")}
        judged = [r for r in scored if r.get("judge") in JUDGE_SCORE]
        if judged:
            out["judge"] = {j: sum(1 for r in judged if r["judge"] == j) for j in JUDGE_SCORE}
            out["judge_score"] = round(statistics.mean(JUDGE_SCORE[r["judge"]] for r in judged), 4)
            out["judge_same"] = round(out["judge"]["same"] / len(judged), 4)
        named = [r for r in scored if r.get("judge_name") in JUDGE_SCORE]
        if named:                                       # the names-only judge, only where it was asked
            out["judge_name"] = {j: sum(1 for r in named if r["judge_name"] == j) for j in JUDGE_SCORE}
            out["judge_name_n"] = len(named)
            out["judge_name_score"] = round(statistics.mean(JUDGE_SCORE[r["judge_name"]] for r in named), 4)
            out["judge_name_same"] = round(out["judge_name"]["same"] / len(named), 4)
    if walked:
        out["seconds_mean"] = round(statistics.mean(r["seconds"] for r in walked), 2)
        out["seconds_median"] = round(statistics.median(r["seconds"] for r in walked), 2)
        out["seconds_total"] = round(sum(r["seconds"] for r in walked), 1)
        out["completion_tokens_mean"] = round(statistics.mean(r["completion_tokens"] for r in walked), 1)
        out["prompt_tokens_mean"] = round(statistics.mean(r["prompt_tokens"] for r in walked), 1)
    return out


def breakdown(rows: list[dict]) -> dict:
    """Overall, by layer, by kind, and by layer for the program's own functions."""
    out = {"all": aggregate(rows)}
    for layer in ("leaf", "middle", "top"):
        out[layer] = aggregate([r for r in rows if r["layer"] == layer])
    for kind in ("program", "runtime"):
        out[kind] = aggregate([r for r in rows if r["kind"] == kind])
    for layer in ("leaf", "middle", "top"):
        out[f"program_{layer}"] = aggregate([r for r in rows if r["kind"] == "program" and r["layer"] == layer])
    return out


def coverage(rows: list[dict]) -> dict:
    """How many functions to name have a real name in the key, and which ones do not (by their layer)."""
    missing = [r for r in rows if not r["real"]]
    return {"to_name": len(rows), "with_key": len(rows) - len(missing),
            "missing": [{"address": r["address"], "given": r["given"], "layer": r["layer"]} for r in missing]}


# ---------------------------------------------------------------- the judge

JUDGE_SYSTEM = ("You check names that a reverse engineer gave to the functions of a compiled program without its "
                "source code, against the real names from the source. Judge the meaning, not the spelling.")
JUDGE_PROMPT = """Program: {program}
Real name of the function in the source: {real}
Name given by the reverse engineer: {given}
Their one-line summary: {summary}

Do the given name and summary describe the same function as the real name?
- same: the same purpose; a developer would accept the given name for this function.
- related: the right area, or a part of what it does, but too vague, too general, or wrong in an important detail.
- different: it describes something else.
Answer with one word: same, related or different."""


def parse_judge(text: str) -> str | None:
    m = re.search(r"\b(same|related|different)\b", (text or "").lower())
    return m.group(1) if m else None


def judge_one(url: str, model: str, program: str, real: str, given: str, summary: str) -> tuple[str | None, str]:
    body = {"model": model, "temperature": 0.0, "max_tokens": 8, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": JUDGE_SYSTEM},
                         {"role": "user", "content": JUDGE_PROMPT.format(program=program, real=real, given=given,
                                                                         summary=summary or "(none)")}]}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    text = data["choices"][0]["message"].get("content") or ""
    return parse_judge(text), text.strip()[:40]


# The names-only judge: the same system prompt, the program as the set file says it and the real name from the
# PDB, and the given name; nothing the namer wrote besides the name (no summary, no comment), so a pass that
# writes better-sounding summaries cannot raise it without better names. Its answers are `judge_name`, beside
# `judge` (with the summary), never in place of it.
JUDGE_NAMES_PROMPT = """Program: {program}
Real name of the function in the source: {real}
Name given by the reverse engineer: {given}

Does the given name describe the same function as the real name?
- same: the same purpose; a developer would accept the given name for this function.
- related: the right area, or a part of what it does, but too vague, too general, or wrong in an important detail.
- different: it describes something else.
Answer with one word: same, related or different."""


def judge_name_one(url: str, model: str, program: str, real: str, given: str) -> tuple[str | None, str]:
    body = {"model": model, "temperature": 0.0, "max_tokens": 8, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": JUDGE_SYSTEM},
                         {"role": "user", "content": JUDGE_NAMES_PROMPT.format(program=program, real=real, given=given)}]}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    text = data["choices"][0]["message"].get("content") or ""
    return parse_judge(text), text.strip()[:40]


def judge_rows(rows: list[dict], url: str, model: str, program: str):
    for r in rows:
        if r["real"] and r["status"] == "done" and r.get("judge") is None:
            r["judge"], r["judge_text"] = judge_one(url, model, program, r["real_full"] or r["real"], r["given"],
                                                    r["summary"])


# ---------------------------------------------------------------- calibration against hand labels

HAND = ("right", "vague", "wrong")


def calibration(rows: list[dict], labels: dict[str, str], field: str = "judge") -> dict:
    """F1 and the judge against hand labels (address -> right / vague / wrong); `field` "judge_name" for the
    names-only judge (the keys of the answer stay judge_*)."""
    by = {r["address"]: r for r in rows}
    pairs = [(labels[a], by[a]) for a in labels if a in by and by[a]["real"] and by[a]["status"] == "done"]
    out = {"n": len(pairs), "f1_by_label": {}, "judge_by_label": {}}
    for h in HAND:
        sel = [r for lab, r in pairs if lab == h]
        out["f1_by_label"][h] = {"n": len(sel), "mean_f1": round(statistics.mean(r["f1"] for r in sel), 4) if sel else None,
                                 "f1_zero": sum(1 for r in sel if r["f1"] == 0)}
        out["judge_by_label"][h] = {j: sum(1 for r in sel if r.get(field) == j) for j in JUDGE_SCORE}
    agree = [(h, r[field]) for h, r in pairs if r.get(field)]
    m = {"right": "same", "vague": "related", "wrong": "different"}
    if agree:
        out["judge_exact"] = round(sum(1 for h, j in agree if m[h] == j) / len(agree), 4)
        out["judge_kappa"] = round(kappa([HAND.index(h) for h, _ in agree],
                                         [list(m.values()).index(j) for _, j in agree], 3), 4)
    # How well each measure orders the labels: the share of (better, worse) label pairs it puts in order,
    # ties counted half (a concordance, AUC-like; 0.5 is chance).
    rank = {"right": 2, "vague": 1, "wrong": 0}
    js = {"same": 2, "related": 1, "different": 0}
    out["f1_concordance"] = concordance([(rank[h], r["f1"]) for h, r in pairs])
    out["judge_concordance"] = concordance([(rank[h], js[r[field]]) for h, r in pairs if r.get(field)])
    return out


def kappa(a: list[int], b: list[int], k: int) -> float:
    n = len(a)
    if not n:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(i) / n) * (b.count(i) / n) for i in range(k))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def concordance(pairs: list[tuple[int, float]]) -> float | None:
    num = den = 0.0
    for i, (la, sa) in enumerate(pairs):
        for lb, sb in pairs[i + 1:]:
            if la == lb:
                continue
            den += 1
            hi, lo = (sa, sb) if la > lb else (sb, sa)
            num += 1 if hi > lo else 0.5 if hi == lo else 0
    return round(num / den, 4) if den else None


# ---------------------------------------------------------------- running the walk

def load_set(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for b in data["binaries"]:
        key = Path(b["key"])
        out.append({"name": b.get("name") or Path(b["path"]).stem, "path": Path(b["path"]),
                    "key": key if key.is_absolute() else path.parent / key, "program": b.get("program") or Path(b["path"]).name})
    return out


def start_engine(model: str, ctx: int, port: int):
    sys.path.insert(0, str(ROOT))
    from mafdet.llama_backend import LlamaServer, pick_device
    from mafdet.proxy.launch import resolve_model
    path, alias, margs = resolve_model(model)
    engine = LlamaServer(path, pick_device("auto"), ctx=ctx, n_slots=1, port=port,
                         extra_args=margs + ("--alias", alias), log_name="walk-bench-server")
    return engine, alias


def pinned(walk_args: list[str], tree: Path) -> list[str]:
    """`walk_args` as they meant before 2026-11-14, when the walk's defaults changed (no thinking; --revise
    without the first name): the old defaults spelled out, for a tree whose command line knows the switches,
    so a result's recorded walk_args keep reproducing what they measured."""
    cli = (tree / "mafdet" / "cli.py").read_text(encoding="utf-8")
    args = list(walk_args)
    if '"--think"' in cli and not {"--think", "--no-think"} & set(args):
        args.append("--think")
    if ('"--revise-draft"' in cli and any(a.startswith("--revise") for a in args)
            and not {"--revise-draft", "--revise-no-draft"} & set(args)):
        args.append("--revise-draft")
    return args


def walk_once(binary: Path, folder: Path, url: str, alias: str, ctx: int, walk_args: list[str], server: str) -> dict:
    """`mafdet agent --walk` in a fresh MAFDET_HOME under `folder`; returns its book and what it printed."""
    home = folder / "home"
    shutil.rmtree(folder, ignore_errors=True)
    home.mkdir(parents=True)
    real_home = Path(os.environ.get("MAFDET_HOME") or Path.home() / ".mafdet")
    shutil.copy(real_home / "mcp.json", home / "mcp.json")
    cmd = [sys.executable, "-m", "mafdet", "agent", "--walk", str(binary), "--mcp", server, "--url", url,
           "--model", alias, "--ctx", str(ctx)] + pinned(walk_args, Path(__file__).resolve().parent.parent)
    env = {**os.environ, "MAFDET_HOME": str(home), "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    t0 = time.monotonic()
    with open(folder / "walk.log", "w", encoding="utf-8") as log:
        log.write(" ".join(cmd) + "\n")
        log.flush()
        code = subprocess.call(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    seconds = time.monotonic() - t0
    books = list((home / "binaries").glob("*/book.json"))
    if len(books) != 1:
        raise RuntimeError(f"the walk of {binary} left {len(books)} books (exit {code}); see {folder / 'walk.log'}")
    shutil.copy(books[0], folder / "book.json")
    return {"book": json.loads(books[0].read_text(encoding="utf-8")), "exit": code, "seconds": round(seconds, 1),
            "command": cmd}


def table(title: str, groups: list[tuple[str, dict]]) -> str:
    head = f"{'':<18}{'n':>5}{'F1':>7}{'P':>7}{'R':>7}{'F1=0':>7}{'judge':>7}{'same':>7}{'s/fn':>7}"
    lines = [title, head]
    for name, a in groups:
        if not a.get("scored"):
            lines.append(f"{name:<18}{a.get('scored', 0):>5}")
            continue
        lines.append(f"{name:<18}{a['scored']:>5}{a['f1']:>7.3f}{a['precision']:>7.3f}{a['recall']:>7.3f}"
                     f"{a['f1_zero']:>7.2f}{a.get('judge_score', float('nan')):>7.3f}{a.get('judge_same', float('nan')):>7.2f}"
                     f"{a.get('seconds_mean', float('nan')):>7.1f}")
    return "\n".join(lines)


def names_table(title: str, groups: list[tuple[str, dict]]) -> str:
    """The names-only judge beside the judge with the summary, per group (only groups it answered in)."""
    lines = [title, f"{'':<18}{'n':>5}{'names':>7}{'same':>7}{'w/ sum':>8}"]
    for name, a in groups:
        if a.get("judge_name_n"):
            lines.append(f"{name:<18}{a['judge_name_n']:>5}{a['judge_name_score']:>7.3f}{a['judge_name_same']:>7.2f}"
                         f"{a.get('judge_score', float('nan')):>8.3f}")
    return "\n".join(lines)


def summarize(result: dict) -> str:
    rows = [r for run in result["runs"] for r in run["functions"]]
    groups = [("all", "all"), ("leaf", "leaf"), ("middle", "middle"), ("top", "top"), ("program", "program"),
              ("runtime", "runtime"), ("program leaf", "program_leaf"), ("program middle", "program_middle"),
              ("program top", "program_top")]
    b = breakdown(rows)
    out = [table(f"walk bench {result['label']}: all runs ({len(result['runs'])})", [(n, b[k]) for n, k in groups])]
    if b["all"].get("judge_name_n"):
        out.append(names_table("the names-only judge (n: names it answered; w/ sum: the judge with the summary, "
                               "over all it answered)", [(n, b[k]) for n, k in groups]))
    out.append(table("per run", [(f"{run['binary']}#{run['repeat']}", run["aggregate"]["all"]) for run in result["runs"]]))
    cross = judge_by_f1(rows)
    if cross:
        lines = ["the judge against F1 (scored functions)", f"{'F1':<8}" + "".join(f"{j:>11}" for j in JUDGE_SCORE)]
        lines += [f"{b:<8}" + "".join(f"{cross[b][j]:>11}" for j in JUDGE_SCORE) for b in ("high", "some", "none")]
        out.append("\n".join(lines))
    return "\n\n".join(out)


def judge_by_f1(rows: list[dict]) -> dict:
    """Cross table: F1 bucket (high >= 0.5, some > 0, none) by the judge's answer."""
    judged = [r for r in rows if r["real"] and r["status"] == "done" and r.get("judge") in JUDGE_SCORE]
    if not judged:
        return {}
    return {b: {j: sum(1 for r in judged if f1_bucket(r["f1"]) == b and r["judge"] == j) for j in JUDGE_SCORE}
            for b in ("high", "some", "none")}


def rescore(result: dict, keys: dict[str, dict] | None = None) -> dict:
    """F1, layers and aggregates computed again from the books' rows (the judge's answers kept)."""
    for run in result["runs"]:
        judged = {r["address"]: (r.get("judge"), r.get("judge_text")) for r in run["functions"]}
        named = {r["address"]: (r["judge_name"], r.get("judge_name_text")) for r in run["functions"] if "judge_name" in r}
        if keys and run["binary"] in keys and run.get("book"):
            run["functions"] = score_book(run["book"], keys[run["binary"]])
        else:
            for r in run["functions"]:
                scored = score_name(r["real"], r.get("real_full")) if r["real"] else ""
                p, rc, f1 = prf(r["given"], scored) if (r["real"] and r["status"] == "done") else (0.0, 0.0, 0.0)
                r.update(precision=round(p, 4), recall=round(rc, 4), f1=round(f1, 4), real_tokens=tokens(scored),
                         given_tokens=tokens(r["given"]), kind=("runtime" if is_runtime(r["real"]) else "program")
                         if r["real"] else None)
        for r in run["functions"]:
            r["judge"], r["judge_text"] = judged.get(r["address"], (None, None))
            if r["address"] in named:
                r["judge_name"], r["judge_name_text"] = named[r["address"]]
        run["aggregate"] = breakdown(run["functions"])
    rows = [r for run in result["runs"] for r in run["functions"]]
    result["aggregate"] = breakdown(rows)
    result["by_binary"] = {}
    for name in dict.fromkeys(run["binary"] for run in result["runs"]):
        result["by_binary"][name] = breakdown([r for run in result["runs"] if run["binary"] == name
                                               for r in run["functions"]])
    reps = sorted({run["repeat"] for run in result["runs"]})
    result["by_repeat"] = {str(k): breakdown([r for run in result["runs"] if run["repeat"] == k
                                              for r in run["functions"]])["all"] for k in reps}
    return result


def cmd_run(a) -> int:
    sets = load_set(Path(a.set))
    if a.only:
        wanted = set(a.only.split(","))
        sets = [s for s in sets if s["name"] in wanted]
    out_path = Path(a.out) if a.out else DATA / f"walk_bench_{a.label}.json"
    work = Path(a.work) if a.work else Path(tempfile.gettempdir()) / "mafdet-walk-bench" / a.label
    walk_args = a.walk_args.split() if a.walk_args else []
    result = {"label": a.label, "set": str(a.set), "model": a.model, "ctx": a.ctx, "walk_args": walk_args,
              "server": a.server, "judge": not a.no_judge, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    if a.append and out_path.is_file():
        result = json.loads(out_path.read_text(encoding="utf-8"))
    keys = {s["name"]: json.loads(s["key"].read_text(encoding="utf-8")) for s in sets}
    engine = None
    try:
        if a.url:
            url, alias = a.url, a.model
        else:
            engine, alias = start_engine(a.model, a.ctx, a.port)
            url = engine.url
        result["alias"] = alias
        first = 1 + max((run["repeat"] for run in result["runs"]), default=0) if a.append else 1
        for rep in range(first, first + a.repeat):
            for s in sets:
                key = keys[s["name"]]
                if key["sha256"] != _sha256(s["path"]):
                    raise SystemExit(f"{s['path']} is not the binary of {s['key']}")
                folder = work / f"{s['name']}-r{rep}"
                print(f"[{time.strftime('%H:%M:%S')}] walk {s['name']} #{rep} -> {folder}", flush=True)
                w = walk_once(s["path"], folder, url, alias, a.ctx, walk_args, a.server)
                rows = score_book(w["book"], key)
                t = time.monotonic()
                if not a.no_judge:
                    judge_rows(rows, url, alias, s["program"])
                run = {"binary": s["name"], "repeat": rep, "exit": w["exit"], "seconds": w["seconds"],
                       "judge_seconds": round(time.monotonic() - t, 1), "command": w["command"],
                       "walk_runs": w["book"].get("runs"), "coverage": coverage(rows), "functions": rows,
                       "book": w["book"] if a.keep_books else None, "folder": str(folder)}
                run["aggregate"] = breakdown(rows)
                result["runs"].append(run)
                al = run["aggregate"]["all"]
                print(f"  {al['named']}/{al['functions']} named, {al['failed']} failed, F1 {al.get('f1', 0):.3f}, "
                      f"judge {al.get('judge_score', float('nan')):.3f}, {w['seconds']:.0f} s walk, "
                      f"{run['judge_seconds']:.0f} s judge", flush=True)
                rescore(result)
                result["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
                out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    finally:
        if engine is not None:
            engine.close()
    print(summarize(result))
    print(f"\n{out_path}")
    return 0


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cmd_score(a) -> int:
    for p in a.results:
        path = Path(p)
        result = json.loads(path.read_text(encoding="utf-8"))
        keys = None
        if any(run.get("book") for run in result["runs"]):
            keys = {s["name"]: json.loads(s["key"].read_text(encoding="utf-8")) for s in load_set(Path(a.set))}
        second = result.get("kind") == "second_pass"
        (rescore_second if second else rescore)(result, keys)
        if a.write:
            path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        print((summarize_second if second else summarize)(result) + "\n")
    return 0


def cmd_book(a) -> int:
    """One book against one key (e.g. a walk run by hand); --judge asks the engine at --url."""
    book = json.loads(Path(a.book).read_text(encoding="utf-8"))
    key = json.loads(Path(a.key).read_text(encoding="utf-8"))
    if is_two_pass(book):
        return book_second(a, book, key)
    rows = score_book(book, key)
    engine = None
    try:
        if a.judge:
            url = a.url
            try:
                urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3).close()
            except OSError:                             # nothing there: start one for the judge, then stop it
                engine, a.model = start_engine(a.model, a.ctx, int(url.rsplit(":", 1)[-1].strip("/")))
                url = engine.url
            judge_rows(rows, url, a.model, a.program or key["binary"])
    finally:
        if engine is not None:
            engine.close()
    result = {"label": a.label or Path(a.book).stem, "runs": [{"binary": Path(key["binary"]).stem, "repeat": 1,
                                                             "functions": rows, "coverage": coverage(rows),
                                                             "book": None}]}
    rescore(result)
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(summarize(result))
    return 0


def book_second(a, book: dict, key: dict) -> int:
    """`book` for a book a second pass went over: both passes, paired."""
    binary = Path(key["binary"]).stem
    base = json.loads(Path(a.base_book).read_text(encoding="utf-8")) if getattr(a, "base_book", "") else None
    run = second_run(binary, book, key, base)
    cache = judge_cache([json.loads(Path(p).read_text(encoding="utf-8")) for p in getattr(a, "judge_from", [])])
    engine = None
    try:
        ask = None
        if a.judge:
            url = a.url
            try:
                urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3).close()
            except OSError:
                engine, a.model = start_engine(a.model, a.ctx, int(url.rsplit(":", 1)[-1].strip("/")))
                url = engine.url
            program = a.program or key["binary"]
            ask = lambda r: judge_one(url, a.model, program, r["real_full"] or r["real"], r["given"], r["summary"])
        run["judged"] = fill_judgements(binary, run["pass1"], run["functions"], cache, ask)
    finally:
        if engine is not None:
            engine.close()
    if base is not None:
        run["base_book_costs"] = base_costs(base)
    result ={"label": a.label or Path(a.book).stem, "kind": "second_pass", "runs": [run]}
    rescore_second(result)
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(summarize_second(result))
    return 0


def calibration_line(name: str, c: dict) -> str:
    f, j = c["f1_by_label"], c["judge_by_label"]
    return (f"{name}: n {c['n']}; mean F1 right/vague/wrong " + " / ".join(
        f"{f[h]['mean_f1']:.3f}" if f[h]["mean_f1"] is not None else "-" for h in HAND)
        + f" (n {'/'.join(str(f[h]['n']) for h in HAND)}); judge same/related/different by label "
        + "; ".join(f"{h} {j[h]['same']}/{j[h]['related']}/{j[h]['different']}" for h in HAND)
        + f"; exact {c.get('judge_exact')}, kappa {c.get('judge_kappa')}; concordance F1 {c['f1_concordance']}, "
          f"judge {c['judge_concordance']}")


def cmd_calibrate(a) -> int:
    if getattr(a, "judge_field", "judge") != "judge" or getattr(a, "by_layer", False):
        return calibrate_fields(a)
    labels_all = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    pooled_rows, pooled_labels, out = [], {}, {}
    for p in a.results:
        result = json.loads(Path(p).read_text(encoding="utf-8"))
        for run in result["runs"]:
            given = {r["address"]: r["given"] for r in run["functions"]}
            # A label is for the name it was given to: another run's name for the same function is not labelled.
            labels = {k: v["label"] for k, v in labels_all.get(run["binary"], {}).items()
                      if given.get(k) == v.get("given")}
            if not labels:
                continue
            c = calibration(run["functions"], labels)
            out[f"{Path(p).stem} {run['binary']}#{run['repeat']}"] = c
            for r in run["functions"]:
                uid = f"{run['binary']}:{r['address']}:{r['given']}"     # the same labelled name counted once
                if r["address"] in labels and uid not in pooled_labels:
                    pooled_rows.append({**r, "address": uid})
                    pooled_labels[uid] = labels[r["address"]]
    if pooled_rows:
        out["pooled"] = calibration(pooled_rows, pooled_labels)
    for name, c in out.items():
        print(calibration_line(name, c))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


def labelled_rows(paths: list[str], labels_all: dict) -> tuple[list[dict], dict[str, str]]:
    """The labelled rows of results, pooled (a labelled name counted once), each row's address made unique."""
    rows, labels = [], {}
    for p in paths:
        result = json.loads(Path(p).read_text(encoding="utf-8"))
        for run in result["runs"]:
            given = {r["address"]: r["given"] for r in run["functions"]}
            mine = {k: v["label"] for k, v in labels_all.get(run["binary"], {}).items() if given.get(k) == v.get("given")}
            for r in run["functions"]:
                uid = f"{run['binary']}:{r['address']}:{r['given']}"
                if r["address"] in mine and uid not in labels:
                    rows.append({**r, "address": uid})
                    labels[uid] = mine[r["address"]]
    return rows, labels


def calibrate_fields(a) -> int:
    """`calibrate --judge-field judge_name|both [--by-layer]`: the names-only judge against the hand labels, on
    the labelled names it answered for (with `both`: on the names both judges answered, the two side by side),
    pooled, and by layer."""
    labels_all = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    rows, labels = labelled_rows(a.results, labels_all)
    fields = {"judge": ["judge"], "judge_name": ["judge_name"], "both": ["judge", "judge_name"]}[a.judge_field]
    rows = [r for r in rows if all(r.get(f) in JUDGE_SCORE for f in fields)]
    out = {}
    groups = [("pooled", lambda r: True)] + ([(layer, lambda r, x=layer: r["layer"] == x)
                                              for layer in ("leaf", "middle", "top")] if a.by_layer else [])
    for gname, sel in groups:
        sub = [r for r in rows if sel(r)]
        for f in fields:
            c = calibration(sub, {r["address"]: labels[r["address"]] for r in sub}, f)
            out[f"{gname} [{f}]"] = c
            print(calibration_line(f"{gname} [{'with summary' if f == 'judge' else 'names only'}]", c))
    if not rows:
        print("no labelled name has an answer of " + " and ".join(fields) + ": rejudge a labelled result first")
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


def paired(rows_a: list[dict], rows_b: list[dict], value, n_boot: int = 2000, seed: int = 0) -> dict:
    """B minus A over the functions both scored (same binary and address), with a bootstrap 95% interval of
    the mean difference (functions resampled). `value(row)` -> a number, or None to leave the row out."""
    import random
    a = {(r["binary"], r["address"]): value(r) for r in rows_a}
    b = {(r["binary"], r["address"]): value(r) for r in rows_b}
    common = [k for k in a if k in b and a[k] is not None and b[k] is not None]
    if not common:
        return {"n": 0}
    d = [b[k] - a[k] for k in common]
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(n_boot))
    return {"n": len(d), "a": round(statistics.mean(a[k] for k in common), 4),
            "b": round(statistics.mean(b[k] for k in common), 4), "diff": round(statistics.mean(d), 4),
            "ci95": [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot) - 1], 4)]}


def paired_judge_name(rows_a: list[dict], rows_b: list[dict], n_boot: int = 2000, seed: int = 0) -> dict:
    """The names-only judge, B minus A, per function scored on both sides. A function judged on both sides
    counts its difference; one whose given name is the same on both sides and was judged on neither (rejudge
    --only-changed) counts 0, as a same name must; any other is left out. `a` and `b` are the means over the
    judged ones, `diff` and its bootstrap interval over all counted, `diff_judged` over the judged ones."""
    import random
    ok = lambda r: r["real"] and r["status"] == "done"
    bk = {(r["binary"], r["address"]): r for r in rows_b}
    d, va, vb, filled = [], [], [], 0
    for x in rows_a:
        y = bk.get((x["binary"], x["address"]))
        if y is None or not (ok(x) and ok(y)):
            continue
        a, b = JUDGE_SCORE.get(x.get("judge_name")), JUDGE_SCORE.get(y.get("judge_name"))
        if a is not None and b is not None:
            d.append(b - a)
            va.append(a)
            vb.append(b)
        elif x["given"] and x["given"] == y["given"] and x.get("judge_name") is None and y.get("judge_name") is None:
            d.append(0.0)
            filled += 1
    if not d:
        return {"n": 0}
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(n_boot))
    out = {"n": len(d), "judged": len(va), "filled": filled, "diff": round(statistics.mean(d), 4),
           "ci95": [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot) - 1], 4)]}
    if va:
        out.update(a=round(statistics.mean(va), 4), b=round(statistics.mean(vb), 4),
                   diff_judged=round(statistics.mean(y - x for x, y in zip(va, vb)), 4))
    return out


def judge_name_text(jn: dict) -> str:
    head = (f"names-only judge {jn['a']:.3f} -> {jn['b']:.3f} over {jn['judged']} judged" if jn.get("judged")
            else "names-only judge, none judged")
    return (head + f"; {jn['diff']:+.3f} (95% {jn['ci95'][0]:+.3f}..{jn['ci95'][1]:+.3f}) over n {jn['n']}"
            + (f", {jn['filled']} unchanged names not judged counted as 0" if jn.get("filled") else ""))


def result_rows(result: dict, repeat: int | None = None, which: str = "functions") -> list[dict]:
    """A result's rows, each with its binary; `which` "pass1" for a second-pass result's first reading."""
    return [{**r, "binary": run["binary"]} for run in result["runs"] if repeat is None or run["repeat"] == repeat
            for r in run.get(which) or run["functions"]]


def cmd_compare(a) -> int:
    """Two results, function by function: F1 and the judge's score, B minus A, with bootstrap intervals."""
    ra, rb = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.a, a.b))
    p1 = getattr(a, "pass1", "") or ""
    rows_a = result_rows(ra, a.repeat_a, "pass1" if "a" in p1 else "functions")
    rows_b = result_rows(rb, a.repeat_b, "pass1" if "b" in p1 else "functions")
    if any("read_again" in r for r in rows_a + rows_b):
        return compare_second(ra, rb, rows_a, rows_b)
    ok = lambda r: r["real"] and r["status"] == "done"
    groups = {"all": lambda r: True, "program": lambda r: r["kind"] == "program", "runtime": lambda r: r["kind"] == "runtime",
              "leaf": lambda r: r["layer"] == "leaf", "middle": lambda r: r["layer"] == "middle", "top": lambda r: r["layer"] == "top"}
    print(f"B ({ra['label']} -> {rb['label']}) minus A, functions scored in both")
    for name, sel in groups.items():
        f1 = paired([r for r in rows_a if sel(r)], [r for r in rows_b if sel(r)], lambda r: r["f1"] if ok(r) else None)
        jd = paired([r for r in rows_a if sel(r)], [r for r in rows_b if sel(r)],
                    lambda r: JUDGE_SCORE.get(r.get("judge")) if ok(r) else None)
        if f1["n"]:
            print(f"  {name:<8} n {f1['n']:>3}  F1 {f1['a']:.3f} -> {f1['b']:.3f} ({f1['diff']:+.3f}, 95% {f1['ci95'][0]:+.3f}"
                  f"..{f1['ci95'][1]:+.3f})  judge {jd.get('a', float('nan')):.3f} -> {jd.get('b', float('nan')):.3f} "
                  f"({jd.get('diff', float('nan')):+.3f}, 95% {jd.get('ci95', [0, 0])[0]:+.3f}..{jd.get('ci95', [0, 0])[1]:+.3f})")
    same = [1 for x in rows_a for y in rows_b if x["binary"] == y["binary"] and x["address"] == y["address"]
            and x["given"] and x["given"] == y["given"]]
    print(f"  the same given name in both: {len(same)} functions")
    if any("judge_name" in r for r in rows_a) and any("judge_name" in r for r in rows_b):
        print("  the names-only judge (a name the same on both sides and judged on neither counts as no difference):")
        for name, sel in groups.items():
            jn = paired_judge_name([r for r in rows_a if sel(r)], [r for r in rows_b if sel(r)])
            if jn["n"]:
                print(f"  {name:<8} {judge_name_text(jn)}")
    return 0


def compare_second(ra: dict, rb: dict, rows_a: list[dict], rows_b: list[dict]) -> int:
    """`compare` when a side is a second pass (two second passes on the same homes: --revise against --refine,
    or two repeats of one): every group, then only the functions both read again (a side without the field
    counts all its functions as read). A repeat is paired by function; give --repeat-a/-b when there are several."""
    print(pairs_text(f"B ({rb['label']}) minus A ({ra['label']}), paired per function", pass_pairs(rows_a, rows_b)))
    both = lambda x, y: x.get("read_again", True) and y.get("read_again", True)
    pp = {k: v for k, v in pass_pairs(rows_a, rows_b, only=both).items() if k in PAIR_GROUPS}
    print(pairs_text("  only the functions both read again", pp))
    by_b = {(r["binary"], r["address"]): r for r in rows_b}
    common = [(x, by_b[(x["binary"], x["address"])]) for x in rows_a if (x["binary"], x["address"]) in by_b]
    same = [(x, y) for x, y in common if x["given"] and x["given"] == y["given"]]
    split = [(x, y) for x, y in same if x.get("judge") in JUDGE_SCORE and y.get("judge") in JUDGE_SCORE
             and x["judge"] != y["judge"]]
    print(f"  the same given name in both: {len(same)} of {len(common)} functions"
          + (f"; judged differently: {len(split)} (judge one side with --judge-from the other)" if split else ""))
    return 0


# ---------------------------------------------------------------- the second pass (--revise, --refine)
#
# A book a second pass went over scores as two results: pass 1 (the first reading: `first.name` where the
# second pass kept it, else `name`) and pass 2 (`name`). Read from the book's JSON only, so it scores the
# local `--revise` (`first` + `revision` {status, renamed, seconds, tokens, with}) and upstream's `--refine`
# (`first` only when something changed, `refine` "kept" / "renamed" / "failed: ...", `refine_seconds`, its
# tokens added to the function's own) alike, and any other pass that keeps `first`.

PAIR_GROUPS = {"all": lambda r: True, "program": lambda r: r["kind"] == "program",
               "runtime": lambda r: r["kind"] == "runtime", "leaf": lambda r: r["layer"] == "leaf",
               "middle": lambda r: r["layer"] == "middle", "top": lambda r: r["layer"] == "top",
               "program top": lambda r: r["kind"] == "program" and r["layer"] == "top"}


def second_status(f: dict) -> str | None:
    """What a second pass did with a function: "done" (read again and answered: kept or renamed),
    "failed", "skipped", or None (not read again)."""
    rev = f.get("revision")
    if isinstance(rev, dict) and rev.get("status"):
        return str(rev["status"])
    ref = f.get("refine")
    if isinstance(ref, str) and ref:
        return "failed" if ref.startswith("failed") else "done"
    if isinstance(ref, dict) and ref.get("status"):
        return str(ref["status"])
    return "done" if f.get("first") else None


def is_two_pass(book: dict) -> bool:
    return any(second_status(f) for f in (book.get("functions") or {}).values())


def pass_book(book: dict, which: int) -> dict:
    """The book as one pass left it: pass 1 puts the first reading back (`first`), pass 2 is the book."""
    if which == 2:
        return book
    fs = {}
    for a, f in book["functions"].items():
        first = f.get("first") if isinstance(f.get("first"), dict) else None
        if first:
            f = {**f, **{k: first[k] for k in ("name", "summary", "comment", "confidence") if first.get(k)}}
        fs[a] = f
    return {**book, "functions": fs}


def root_depths(functions: dict) -> dict[str, int]:
    """Calls below a root (0) for every function reached, as a top-down pass sees it: roots are the functions
    nobody calls (a named one only with code and callees: `entry`); a cycle nobody calls starts at its lowest
    address. As book.Book.depths, recomputed here so the score does not import either tree's code."""
    fs = functions

    def code(f):
        return f.get("status") != "known" or not ((f.get("flags") or {}).get("external") or
                                                  (f.get("flags") or {}).get("thunk")
                                                  or str(f.get("address", "")).startswith("EXTERNAL:"))
    called = {c for a, f in fs.items() for c in f.get("callees") or [] if c != a}
    order = sorted(fs, key=lambda x: (0, int(x, 16), "") if re.fullmatch(r"0x[0-9a-fA-F]+", x) else (1, 0, x))
    roots = [a for a in order if a not in called and (fs[a].get("status") != "known" or code(fs[a])
                                                      and fs[a].get("callees"))]
    depth: dict[str, int] = {}

    def spread(seeds):
        queue = list(seeds)
        for s in seeds:
            depth[s] = 0
        i = 0
        while i < len(queue):
            a = queue[i]
            i += 1
            for c in fs[a].get("callees") or []:
                if c in fs and c not in depth:
                    depth[c] = depth[a] + 1
                    queue.append(c)
    spread(roots)
    for a in order:
        if a not in depth and code(fs[a]):
            spread([a])
    return depth


def second_cost(f: dict, base: dict | None) -> dict:
    """What the second pass spent on a function: seconds and tokens (local --revise: `revision`; --refine:
    `refine_seconds`, and its tokens as the book's minus the first pass's, when the first pass's book is given)."""
    rev = f.get("revision") if isinstance(f.get("revision"), dict) else {}
    if rev:
        return {"seconds": rev.get("seconds") or 0.0, "prompt_tokens": rev.get("prompt_tokens"),
                "completion_tokens": rev.get("completion_tokens"), "steps": rev.get("steps"), "with": rev.get("with")}
    out = {"seconds": f.get("refine_seconds") or 0.0, "prompt_tokens": None, "completion_tokens": None}
    if base is not None and second_status(f):
        for k in ("prompt_tokens", "completion_tokens"):
            out[k] = (f.get(k) or 0) - (base.get(k) or 0)
    return out


def second_rows(book: dict, key: dict, base_book: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(pass 1 rows, pass 2 rows) of one book, both as `score_book` makes them. A pass-2 row also says
    whether the function was read again, renamed (its pass-1 name differs), its depth from the roots and
    what its second reading cost; the cost fields both rows share (seconds, tokens) are the first pass's
    (the base book's when given: --refine adds its tokens to them)."""
    rows1 = score_book(pass_book(book, 1), key)
    rows2 = score_book(book, key)
    fs, depth = book["functions"], root_depths(book["functions"])
    base = (base_book or {}).get("functions") or {}
    for r1, r2 in zip(rows1, rows2):
        a = r2["address"]
        f, b = fs[a], base.get(a)
        if b is not None:
            for r in (r1, r2):
                r.update(prompt_tokens=b.get("prompt_tokens") or 0, completion_tokens=b.get("completion_tokens") or 0,
                         seconds=b.get("seconds") or 0.0)
        status = second_status(f)
        r2.update(first_given=r1["given"], read_again=status is not None, second_status=status,
                  renamed=bool(r1["given"]) and r1["given"] != r2["given"], depth=depth.get(a),
                  second=second_cost(f, b) if status else None)
    return rows1, rows2


def program_info(book: dict) -> dict:
    """The second pass's summary of the program, and what it cost (as much as the book says)."""
    p = book.get("program") or {}
    if not isinstance(p, dict) or not p:
        return {}
    out = {k: p.get(k) for k in ("status", "summary", "seconds", "steps", "prompt_tokens", "completion_tokens",
                                 "inputs", "reason", "strings_error") if p.get(k) not in (None, "")}
    if p.get("facts"):
        out["facts_chars"] = len(p["facts"])
    return out


def judge_key(binary: str, r: dict) -> tuple:
    return binary, r["address"], r["given"]


def judge_cache(results: list[dict], field: str = "judge") -> dict[tuple, tuple]:
    """(binary, address, given name) -> (the judge's answer, its text), from results already judged (the
    baseline's, another second pass's): a name judged once is not judged again. `field` "judge_name" for the
    names-only judge."""
    cache: dict[tuple, tuple] = {}
    for result in results:
        for run in result.get("runs") or []:
            for rows in (run.get("pass1") or [], run.get("functions") or []):
                for r in rows:
                    if r.get(field) in JUDGE_SCORE and r.get("given"):
                        cache.setdefault(judge_key(run["binary"], r), (r[field], r.get(field + "_text")))
    return cache


def fill_judgements(binary: str, rows1: list[dict], rows2: list[dict], cache: dict, ask=None,
                    field: str = "judge") -> int:
    """The judge over both passes: pass 1 from the cache (the baseline judged those names) or asked; a pass-2
    name equal to its pass-1 name gets the pass-1 answer (identical score, whatever its summary became); a
    new name from the cache or asked. `ask(row)` -> (answer, text). Returns how many were asked."""
    asked = 0

    def one(r):
        nonlocal asked
        if not (r["real"] and r["status"] == "done"):
            return
        k = judge_key(binary, r)
        if k not in cache and ask is not None:
            cache[k] = ask(r)
            asked += 1
        r[field], r[field + "_text"] = cache.get(k, (None, None))
    by1 = {r["address"]: r for r in rows1}
    for r in rows1:
        one(r)
    for r in rows2:
        r1 = by1.get(r["address"])
        if r1 is not None and r1["given"] == r["given"] and r["real"] and r["status"] == "done":
            r[field], r[field + "_text"] = r1.get(field), r1.get(field + "_text")
        else:
            one(r)
    return asked


def second_stats(rows1: list[dict], rows2: list[dict]) -> dict:
    """What the second pass did: functions read again, renamed, per depth from the roots and per layer; of
    the renamed ones with a real name, better / worse / same on F1 and on the judge; its cost per function."""
    by1 = {(r.get("binary"), r["address"]): r for r in rows1}
    named = [r for r in rows2 if r["status"] == "done"]
    read = [r for r in named if r.get("read_again")]
    out = {"named": len(named), "read": len(read),
           "read_done": sum(1 for r in read if r.get("second_status") == "done"),
           "read_failed": sum(1 for r in read if r.get("second_status") == "failed"),
           "read_skipped": sum(1 for r in read if r.get("second_status") == "skipped"),
           "renamed": sum(1 for r in named if r.get("renamed")), "by_depth": {}, "by_layer": {}}
    for d in sorted({r.get("depth") for r in named}, key=lambda x: (x is None, x if x is not None else 0)):
        sel = [r for r in named if r.get("depth") == d]
        out["by_depth"][str(d)] = {"n": len(sel), "read": sum(1 for r in sel if r.get("read_again")),
                                   "renamed": sum(1 for r in sel if r.get("renamed"))}
    for layer in ("leaf", "middle", "top"):
        sel = [r for r in named if r["layer"] == layer]
        out["by_layer"][layer] = {"n": len(sel), "read": sum(1 for r in sel if r.get("read_again")),
                                  "renamed": sum(1 for r in sel if r.get("renamed"))}
    changed = [(by1[(r.get("binary"), r["address"])], r) for r in named if r.get("renamed") and r["real"]
               and (r.get("binary"), r["address"]) in by1]

    def tally(value):
        pairs = [(value(a), value(b)) for a, b in changed]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        return {"n": len(pairs), "better": sum(1 for x, y in pairs if y > x), "worse": sum(1 for x, y in pairs if y < x),
                "same": sum(1 for x, y in pairs if y == x)}
    out["renamed_scored"] = len(changed)
    out["renamed_f1"] = tally(lambda r: r["f1"])
    out["renamed_judge"] = tally(lambda r: JUDGE_SCORE.get(r.get("judge")))
    if any("judge_name" in r for r in rows2):
        out["renamed_judge_name"] = tally(lambda r: JUDGE_SCORE.get(r.get("judge_name")))
    costs = [r["second"] for r in read if r.get("second")]
    secs = [c["seconds"] for c in costs if c.get("seconds")]
    if secs:
        out["seconds_mean"] = round(statistics.mean(secs), 2)
        out["seconds_median"] = round(statistics.median(secs), 2)
        out["seconds_total"] = round(sum(secs), 1)
    for k in ("prompt_tokens", "completion_tokens"):
        v = [c[k] for c in costs if c.get(k) is not None]
        if v:
            out[f"{k}_mean"] = round(statistics.mean(v), 1)
    return out


def pass_pairs(rows_a: list[dict], rows_b: list[dict], only=None) -> dict:
    """B minus A, F1 and the judge, paired per function, per group (PAIR_GROUPS, plus `read`: the functions
    read again, in B; and `renamed`). `only(row_a, row_b)` keeps a subset of the pairs."""
    ok = lambda r: r["real"] and r["status"] == "done"
    keep = None
    if only is not None:
        bk = {(r.get("binary"), r["address"]): r for r in rows_b}
        keep = {k for r in rows_a for k in [(r.get("binary"), r["address"])] if k in bk and only(r, bk[k])}
    groups = dict(PAIR_GROUPS)
    names = any("judge_name" in r for r in rows_a) and any("judge_name" in r for r in rows_b)
    if any("read_again" in r for r in rows_b):
        groups["read again"] = lambda r: r.get("read_again", True)
        groups["renamed"] = lambda r: r.get("renamed", True)
    out = {}
    for name, sel in groups.items():
        # A group is chosen on B's rows (read again, renamed) and on A's layer and kind (the same in both).
        ka = {(r.get("binary"), r["address"]) for r in rows_a if name in PAIR_GROUPS and sel(r)}
        kb = {(r.get("binary"), r["address"]) for r in rows_b if sel(r)}
        ks = (ka & kb) if name in PAIR_GROUPS else kb
        if keep is not None:
            ks &= keep
        a = [r for r in rows_a if (r.get("binary"), r["address"]) in ks]
        b = [r for r in rows_b if (r.get("binary"), r["address"]) in ks]
        f1 = paired(a, b, lambda r: r["f1"] if ok(r) else None)
        jd = paired(a, b, lambda r: JUDGE_SCORE.get(r.get("judge")) if ok(r) else None)
        out[name] = {"f1": f1, "judge": jd}
        if names:
            out[name]["judge_name"] = paired_judge_name(a, b)
    return out


def pairs_text(title: str, pairs: dict) -> str:
    lines = [title]
    for name, p in pairs.items():
        f1, jd = p["f1"], p["judge"]
        if not f1.get("n"):
            lines.append(f"  {name:<12} n   0")
            continue
        line = (f"  {name:<12} n {f1['n']:>3}  F1 {f1['a']:.3f} -> {f1['b']:.3f} ({f1['diff']:+.3f}, 95% "
                f"{f1['ci95'][0]:+.3f}..{f1['ci95'][1]:+.3f})")
        if jd.get("n"):
            line += (f"  judge {jd['a']:.3f} -> {jd['b']:.3f} ({jd['diff']:+.3f}, 95% {jd['ci95'][0]:+.3f}.."
                     f"{jd['ci95'][1]:+.3f}, n {jd['n']})")
        lines.append(line)
        if (p.get("judge_name") or {}).get("n"):
            lines.append(f"  {'':<12}        {judge_name_text(p['judge_name'])}")
    return "\n".join(lines)


def second_run(binary: str, book: dict, key: dict, base_book: dict | None = None) -> dict:
    """One two-pass book as a run of a second-pass result: `functions` are pass 2's rows (so `compare` and
    the tables read it as any result), `pass1` the first reading's."""
    rows1, rows2 = second_rows(book, key, base_book)
    return {"binary": binary, "repeat": 1, "functions": rows2, "pass1": rows1, "coverage": coverage(rows2),
            "program": program_info(book), "book": book}


def rescore_second(result: dict, keys: dict[str, dict] | None = None) -> dict:
    """A second-pass result's tables again: both passes, the paired difference, what the pass did. With keys,
    the rows are made again from the kept books (the judge's answers kept, unchanged names sharing them)."""
    for run in result["runs"]:
        if keys and run["binary"] in keys and run.get("book"):
            old = judge_cache([{"runs": [run]}])
            names = any("judge_name" in r for r in run["pass1"] + run["functions"])
            old_names = judge_cache([{"runs": [run]}], "judge_name") if names else {}
            rows1, rows2 = second_rows(run["book"], keys[run["binary"]], run.get("base_book_costs"))
            fill_judgements(run["binary"], rows1, rows2, old)
            if names:
                fill_judgements(run["binary"], rows1, rows2, old_names, field="judge_name")
            run["pass1"], run["functions"] = rows1, rows2
        for rows in (run["pass1"], run["functions"]):
            for r in rows:
                r["binary"] = run["binary"]
        run["aggregate"] = breakdown(run["functions"])
        run["aggregate_pass1"] = breakdown(run["pass1"])
        run["second"] = second_stats(run["pass1"], run["functions"])
    rows1 = [r for run in result["runs"] for r in run["pass1"]]
    rows2 = [r for run in result["runs"] for r in run["functions"]]
    result["aggregate"], result["aggregate_pass1"] = breakdown(rows2), breakdown(rows1)
    result["second"] = second_stats(rows1, rows2)
    result["by_binary"], result["by_binary_pass1"] = {}, {}
    for name in dict.fromkeys(run["binary"] for run in result["runs"]):
        sel = [run for run in result["runs"] if run["binary"] == name]
        result["by_binary"][name] = breakdown([r for run in sel for r in run["functions"]])
        result["by_binary_pass1"][name] = breakdown([r for run in sel for r in run["pass1"]])
    reps = sorted({run["repeat"] for run in result["runs"]})
    result["by_repeat"] = {str(k): breakdown([r for run in result["runs"] if run["repeat"] == k
                                              for r in run["functions"]])["all"] for k in reps}
    # Pass 2 against pass 1, per repeat (a function counted once per repeat: the pairs are per run).
    result["paired"] = {}
    for k in reps:
        sel = [run for run in result["runs"] if run["repeat"] == k]
        result["paired"][str(k)] = pass_pairs([r for run in sel for r in run["pass1"]],
                                              [r for run in sel for r in run["functions"]])
    return result


def summarize_second(result: dict) -> str:
    groups = [("all", "all"), ("leaf", "leaf"), ("middle", "middle"), ("top", "top"), ("program", "program"),
              ("runtime", "runtime"), ("program top", "program_top")]
    b1, b2 = result["aggregate_pass1"], result["aggregate"]
    how = " ".join(result.get("pass_args") or []) or "second pass"
    tree = (result.get("tree") or {}).get("commit", "")[:10]
    out = [f"walk bench {result['label']}: {how}" + (f" (tree {tree})" if tree else "")
           + f", {len(result['runs'])} runs",
           table("pass 1 (the first reading)", [(n, b1[k]) for n, k in groups]),
           table("pass 2", [(n, b2[k]) for n, k in groups]),
           *([names_table("the names-only judge, pass 1", [(n, b1[k]) for n, k in groups]),
              names_table("the names-only judge, pass 2", [(n, b2[k]) for n, k in groups])]
             if b2["all"].get("judge_name_n") or b1["all"].get("judge_name_n") else []),
           table("per run, pass 1 / pass 2", [x for run in result["runs"] for x in
                                              ((f"{run['binary']}#{run['repeat']} p1", run["aggregate_pass1"]["all"]),
                                               (f"{run['binary']}#{run['repeat']} p2", run["aggregate"]["all"]))])]
    for rep, pairs in (result.get("paired") or {}).items():
        out.append(pairs_text(f"pass 2 minus pass 1, paired per function (repeat {rep})", pairs))
    s = result["second"]
    lines = [f"what the second pass did: {s['read']} of {s['named']} named functions read again ({s['read_done']} "
             f"answered, {s['read_failed']} failed, {s['read_skipped']} skipped), {s['renamed']} renamed"]
    lines.append("  by depth from the roots: " + "; ".join(f"{d}: {v['renamed']}/{v['read']}/{v['n']}"
                                                         for d, v in s["by_depth"].items())
                 + "  (renamed / read / named)")
    lines.append("  by layer: " + "; ".join(f"{k} {v['renamed']}/{v['read']}/{v['n']}" for k, v in s["by_layer"].items()))
    f, j = s["renamed_f1"], s["renamed_judge"]
    lines.append(f"  renamed with a real name: {s['renamed_scored']}; F1 better/worse/same {f['better']}/{f['worse']}/"
                 f"{f['same']}" + (f"; judge {j['better']}/{j['worse']}/{j['same']} (n {j['n']})" if j["n"] else ""))
    jn = s.get("renamed_judge_name")
    if jn and jn["n"]:
        lines.append(f"  renamed, names-only judge better/worse/same {jn['better']}/{jn['worse']}/{jn['same']} (n {jn['n']})")
    scored2 = [r for run in result["runs"] for r in run["functions"] if r["real"] and r["status"] == "done"]
    answered = sum(1 for r in scored2 if r.get("judge_name") in JUDGE_SCORE)
    if any("judge_name" in r for r in scored2) and answered < len(scored2):
        lines.append(f"  the names-only judge answered {answered} of {len(scored2)} pass-2 names (rejudge --only-changed): "
                     "its means are over those; an unchanged name not judged counts 0 in its paired difference")
    if s.get("seconds_mean") is not None:
        lines.append(f"  second pass per function: {s['seconds_mean']:.1f} s mean, {s['seconds_median']:.1f} median, "
                     f"{s['seconds_total']:.0f} s in all" + (
                         f"; {s.get('prompt_tokens_mean', 0):.0f}+{s.get('completion_tokens_mean', 0):.0f} tokens"
                         if s.get("prompt_tokens_mean") is not None else ""))
    for run in result["runs"]:
        p = run.get("program") or {}
        if p:
            lines.append(f"  program {run['binary']}#{run['repeat']}: {p.get('status', 'done')}, "
                         f"{p.get('seconds', 0):.1f} s, {p.get('prompt_tokens', '?')}+{p.get('completion_tokens', '?')} "
                         f"tok, inputs {p.get('inputs', '?')}: {str(p.get('summary', ''))[:160]}")
        chk = run.get("first_pass_check")
        if chk and not chk.get("clean"):
            lines.append(f"  ! {run['binary']}#{run['repeat']}: the first pass was not skipped: {chk}")
    out.append("\n".join(lines))
    return "\n\n".join(out)


def first_pass_check(base_book: dict, book: dict, log: str = "") -> dict:
    """Did a second pass on a copy of a finished home leave the first pass alone? The walk's `to do` count
    (0: no first-pass request), and against the copied book: no function named that was not, no first name or
    first-pass cost changed, no function added or lost."""
    bf, nf = base_book["functions"], book["functions"]
    p1 = pass_book(book, 1)["functions"]
    todo = [int(x) for x in re.findall(r"to do\s+(\d+) functions", log)]
    out = {"todo": todo,
           "added": sorted(a for a in nf if a not in bf), "lost": sorted(a for a in bf if a not in nf),
           "status_changed": sorted(a for a in bf if a in nf and bf[a].get("status") != nf[a].get("status")),
           "first_name_changed": sorted(a for a in bf if a in nf and bf[a].get("status") == "done"
                                        and p1[a].get("name") != bf[a].get("name")),
           "first_cost_changed": sorted(a for a in bf if a in nf and bf[a].get("status") == "done"
                                        and (nf[a].get("seconds"), nf[a].get("steps")) != (bf[a].get("seconds"),
                                                                                          bf[a].get("steps")))}
    out["clean"] = bool(todo) and all(t == 0 for t in todo) and not any(
        out[k] for k in ("added", "lost", "status_changed", "first_name_changed", "first_cost_changed"))
    return out


def tree_state(tree: Path) -> dict:
    """The code tree a walk runs from: its commit, whether its mafdet/ has changes, and which mafdet a walk
    started there imports (cwd and PYTHONPATH both point at it: either wins over the venv's editable install,
    checked 2026-11-13); importing mafdet.agent.walk also catches a tree left in a conflicted merge."""
    def git(*args):
        p = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    p = subprocess.run([sys.executable, "-c", "import mafdet, mafdet.agent.walk; print(mafdet.__file__)"],
                       cwd=str(tree), env=tree_env(tree), capture_output=True, text=True)
    imported = p.stdout.strip().splitlines()[-1] if p.returncode == 0 and p.stdout.strip() else ""
    ok = bool(imported) and Path(imported).resolve().parent == (tree / "mafdet").resolve()
    return {"path": str(tree), "commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no", "--", "mafdet")),
            "imports": imported, "ok": ok, "error": "" if ok else (p.stderr.strip()[-400:] or imported)}


def tree_env(tree: Path, home: Path | None = None) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1",
           "PYTHONPATH": str(tree) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    if home is not None:
        env["MAFDET_HOME"] = str(home)
    return env


def walk_second(binary: Path, src: Path, folder: Path, url: str, alias: str, ctx: int, walk_args: list[str],
                server: str, tree: Path) -> dict:
    """The walk again on a COPY of a finished run's home (`src`/home: its book, its mcp.json), from `tree`:
    the first pass is done there, so the walk goes on with what `walk_args` asks (--revise, --refine)."""
    src_books = list((src / "home" / "binaries").glob("*/book.json"))
    if len(src_books) != 1:
        raise SystemExit(f"{src / 'home'} has {len(src_books)} books, not one")
    src_r, folder_r = src.resolve(), folder.resolve()
    if folder_r == src_r or src_r in folder_r.parents or folder_r in src_r.parents:
        raise SystemExit(f"{folder} would overwrite the run it copies ({src})")
    base_book = json.loads(src_books[0].read_text(encoding="utf-8"))
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    home = folder / "home"
    shutil.copytree(src / "home", home)
    cmd = [sys.executable, "-m", "mafdet", "agent", "--walk", str(binary), "--mcp", server, "--url", url,
           "--model", alias, "--ctx", str(ctx)] + pinned(walk_args, tree)
    t0 = time.monotonic()
    with open(folder / "walk.log", "w", encoding="utf-8") as log:
        log.write(" ".join(cmd) + "\n")
        log.flush()
        code = subprocess.call(cmd, cwd=str(tree), env=tree_env(tree, home), stdout=log, stderr=subprocess.STDOUT)
    seconds = time.monotonic() - t0
    books = list((home / "binaries").glob("*/book.json"))
    if len(books) != 1:
        raise RuntimeError(f"the walk of {binary} left {len(books)} books (exit {code}); see {folder / 'walk.log'}")
    shutil.copy(books[0], folder / "book.json")
    return {"book": json.loads(books[0].read_text(encoding="utf-8")), "base_book": base_book, "exit": code,
            "seconds": round(seconds, 1), "command": cmd,
            "log": (folder / "walk.log").read_text(encoding="utf-8", errors="replace")}


def base_costs(base_book: dict) -> dict:
    """The first pass's cost per function, kept in a second-pass run to rescore it without the base book."""
    return {"functions": {a: {k: f.get(k) for k in ("seconds", "prompt_tokens", "completion_tokens")}
                          for a, f in base_book["functions"].items() if f.get("status") == "done"}}


def cmd_second(a) -> int:
    """A second pass on copies of a finished run's homes (the first pass is not paid again), scored as two
    results from each book, pass 2 against pass 1."""
    base_path = Path(a.base_result)
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    model = a.model or base.get("alias") or base.get("model") or "qwen3-14b:IQ4_XS"
    ctx = a.ctx or base.get("ctx") or 16384
    server = a.server or base.get("server") or "rea"
    pass_args, extra = a.pass_args.split(), a.walk_args.split() if a.walk_args else []
    walk_args = list(base.get("walk_args") or []) + pass_args + extra   # the first pass's own options kept
    tree = Path(a.tree).resolve()
    state = tree_state(tree)
    if not state["ok"]:
        raise SystemExit(f"the walk would not import {tree / 'mafdet'}: {state['error']}")
    sets = load_set(Path(a.set))
    if a.only:
        wanted = set(a.only.split(","))
        sets = [s for s in sets if s["name"] in wanted]
    source = Path(getattr(a, "from"))
    work = Path(a.work)
    out_path = Path(a.out) if a.out else DATA / f"walk_bench_{a.label}.json"
    cache = judge_cache([base] + [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.judge_from])
    result = {"label": a.label, "kind": "second_pass", "set": str(a.set), "from": str(source),
              "base_result": str(base_path), "base_repeat": a.base_repeat, "model": model, "ctx": ctx,
              "server": server, "pass_args": pass_args, "walk_args": walk_args, "tree": state,
              "judge": not a.no_judge, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    keys = {s["name"]: json.loads(s["key"].read_text(encoding="utf-8")) for s in sets}
    print(f"tree {tree} at {state['commit'][:10]}{' (mafdet/ changed)' if state['dirty'] else ''}, "
          f"walk args {' '.join(walk_args)}", flush=True)
    engine = None
    try:
        if a.url:
            url, alias = a.url, model
        else:
            engine, alias = start_engine(model, ctx, a.port)
            url = engine.url
        result["alias"] = alias
        for rep in range(1, 1 + a.repeat):
            for s in sets:
                key = keys[s["name"]]
                if key["sha256"] != _sha256(s["path"]):
                    raise SystemExit(f"{s['path']} is not the binary of {s['key']}")
                src = source / f"{s['name']}-r{a.base_repeat}"
                folder = work / f"{s['name']}-r{rep}"
                print(f"[{time.strftime('%H:%M:%S')}] second pass {s['name']} #{rep}: {src} -> {folder}", flush=True)
                w = walk_second(s["path"], src, folder, url, alias, ctx, walk_args, server, tree)
                run = second_run(s["name"], w["book"], key, w["base_book"])
                run.update(repeat=rep, exit=w["exit"], seconds=w["seconds"], command=w["command"], folder=str(folder),
                           source=str(src), walk_runs=w["book"].get("runs", [])[len(w["base_book"].get("runs", [])):],
                           first_pass_check=first_pass_check(w["base_book"], w["book"], w["log"]),
                           base_book_costs=base_costs(w["base_book"]))
                t = time.monotonic()
                ask = None if a.no_judge else (
                    lambda r, p=s["program"]: judge_one(url, alias, p, r["real_full"] or r["real"], r["given"], r["summary"]))
                run["judged"] = fill_judgements(s["name"], run["pass1"], run["functions"], cache, ask)
                run["judge_seconds"] = round(time.monotonic() - t, 1)
                result["runs"].append(run)
                rescore_second(result)
                chk = run["first_pass_check"]
                st = run["second"]
                print(f"  {st['read']}/{st['named']} read again, {st['renamed']} renamed, F1 "
                      f"{run['aggregate_pass1']['all'].get('f1', 0):.3f} -> {run['aggregate']['all'].get('f1', 0):.3f}, "
                      f"{w['seconds']:.0f} s walk, {run['judged']} judged in {run['judge_seconds']:.0f} s"
                      + ("" if chk["clean"] else f"\n  ! THE FIRST PASS WAS NOT SKIPPED: {chk}"), flush=True)
                result["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
                out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    finally:
        if engine is not None:
            engine.close()
    print(summarize_second(result))
    print(f"\n{out_path}")
    return 0


# ---------------------------------------------------------------- rejudge: the names-only judge, after the fact

def rejudge_plan(results: list[dict], cache: dict, only_changed: bool = False) -> dict[tuple, tuple]:
    """The names the names-only judge has to be asked for, once each across all the results: key (binary,
    address, given name) -> (binary, row). A second-pass result: its pass-1 names and the pass-2 names that
    differ from them (a same name is answered once for both); with `only_changed`, only the functions whose
    name changed (both their names). A name already answered (in `cache`) is not asked."""
    todo: dict[tuple, tuple] = {}
    ok = lambda r: r is not None and r["real"] and r["status"] == "done" and r.get("given")

    def want(b, r):
        if ok(r):
            k = judge_key(b, r)
            if k not in cache:
                todo.setdefault(k, (b, r))
    for result in results:
        for run in result.get("runs") or []:
            b = run["binary"]
            if result.get("kind") == "second_pass":
                by2 = {r["address"]: r for r in run["functions"]}
                for r1 in run["pass1"]:
                    r2 = by2.get(r1["address"])
                    changed = r2 is not None and r2["given"] != r1["given"]
                    if changed or not only_changed:
                        want(b, r1)
                    if changed:
                        want(b, r2)
            else:
                for r in run["functions"]:
                    want(b, r)
    return todo


def apply_names(result: dict, cache: dict):
    """The names-only answers into a result's rows (pass 2 sharing pass 1's for a same name), then its tables."""
    for run in result["runs"]:
        if result.get("kind") == "second_pass":
            fill_judgements(run["binary"], run["pass1"], run["functions"], cache, field="judge_name")
        else:
            for r in run["functions"]:
                if r["real"] and r["status"] == "done":
                    r["judge_name"], r["judge_name_text"] = cache.get(judge_key(run["binary"], r), (None, None))
    (rescore_second if result.get("kind") == "second_pass" else rescore)(result)


def _write_atomic(path: Path, result: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def cmd_rejudge(a) -> int:
    """`rejudge <result.json>... --names-only [--only-changed] [--dry-run]`: the names-only judge added to results
    already made (never in place of `judge`), each name asked once across all the files given (and never again
    once a file holds its answer: give the files already rejudged with the new ones to reuse theirs). Rewrites the
    files: not one a run is still writing."""
    if not a.names_only:
        raise SystemExit("rejudge asks the names-only judge: give --names-only (the judge with the summary is run's)")
    paths = [Path(p) for p in a.results]
    results = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    programs = {s["name"]: s["program"] for s in load_set(Path(a.set))}
    cache = judge_cache(results, "judge_name")
    todo = rejudge_plan(results, cache, a.only_changed)
    for path, result in zip(paths, results):
        mine = rejudge_plan([result], cache, a.only_changed)
        print(f"{path.name}: {len(mine)} names to ask" + (" (second pass" + (", changed names only)" if a.only_changed
                                                                           else ")") if result.get("kind") == "second_pass" else ""))
    print(f"{len(todo)} names to ask in all, {len(cache)} answered already")
    if a.dry_run:
        return 0
    engine = None
    t0 = time.monotonic()
    try:
        if todo:
            url, model = a.url, a.model
            try:
                urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3).close()
            except OSError:                             # nothing there: start one for the judge, then stop it
                engine, model = start_engine(a.model, a.ctx, int(url.rsplit(":", 1)[-1].strip("/")))
                url = engine.url
            for i, (k, (b, r)) in enumerate(todo.items(), 1):
                cache[k] = judge_name_one(url, model, programs.get(b, b), r["real_full"] or r["real"], r["given"])
                if i % 50 == 0:
                    print(f"  {i}/{len(todo)} asked, {time.monotonic() - t0:.0f} s", flush=True)
    finally:
        if engine is not None:
            engine.close()
    seconds = round(time.monotonic() - t0, 1)
    for path, result in zip(paths, results):
        apply_names(result, cache)
        result["judge_name"] = {"prompt": "names only (JUDGE_NAMES_PROMPT)", "model": a.model,
                                "only_changed": bool(a.only_changed), "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "asked_in_command": len(todo), "seconds_in_command": seconds}
        _write_atomic(path, result)
        if result.get("kind") == "second_pass":
            for rep_, pairs in (result.get("paired") or {}).items():
                print(pairs_text(f"{path.name}: pass 2 minus pass 1 (repeat {rep_})", pairs))
        else:
            al = result["aggregate"]
            print(f"{path.name}: names-only judge " + ", ".join(
                f"{g} {al[g]['judge_name_score']:.3f} (n {al[g]['judge_name_n']}; with the summary "
                f"{al[g].get('judge_score', float('nan')):.3f})" for g in ("all", "leaf", "middle", "top")
                if al[g].get("judge_name_n")))
    print(f"{len(todo)} names asked in {seconds:.0f} s")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="walk the set and score it")
    r.add_argument("label")
    r.add_argument("--set", default=str(DEFAULT_SET))
    r.add_argument("--only", default="", help="comma-separated binary names of the set")
    r.add_argument("--repeat", type=int, default=1, help="walk each binary this many times")
    r.add_argument("--append", action="store_true", help="add the runs to an existing result of the same label")
    r.add_argument("--model", default="qwen3-14b:IQ4_XS")
    r.add_argument("--ctx", type=int, default=16384)
    r.add_argument("--port", type=int, default=8079)
    r.add_argument("--url", default="", help="an engine already running (then --model is its alias)")
    r.add_argument("--server", default="rea", help="the MCP server of mcp.json the walk reads the binary through")
    r.add_argument("--walk-args", default="", help='more options for the walk: --walk-args="--no-think"')
    r.add_argument("--no-judge", action="store_true")
    r.add_argument("--keep-books", action="store_true", help="keep each run's whole book in the result")
    r.add_argument("--work", default="", help="where the runs' homes, books and logs go (default: the temp folder)")
    r.add_argument("--out", default="")
    s = sub.add_parser("score", help="rescore result files (no GPU)")
    s.add_argument("results", nargs="+")
    s.add_argument("--set", default=str(DEFAULT_SET))
    s.add_argument("--write", action="store_true")
    b = sub.add_parser("book", help="score one book against one key")
    b.add_argument("book")
    b.add_argument("key")
    b.add_argument("--judge", action="store_true")
    b.add_argument("--url", default="http://127.0.0.1:8079")
    b.add_argument("--model", default="qwen3-14b:IQ4_XS")
    b.add_argument("--program", default="")
    b.add_argument("--ctx", type=int, default=16384)
    b.add_argument("--label", default="")
    b.add_argument("--out", default="")
    b.add_argument("--base-book", default="", help="a two-pass book: the book before its second pass (costs)")
    b.add_argument("--judge-from", nargs="*", default=[], help="a two-pass book: results whose judged names are reused")
    c = sub.add_parser("calibrate", help="F1 and the judge against hand labels")
    c.add_argument("results", nargs="+")
    c.add_argument("--labels", default=str(DATA / "walk_bench_labels.json"))
    c.add_argument("--out", default="")
    c.add_argument("--judge-field", choices=("judge", "judge_name", "both"), default="judge",
                   help="judge_name: the names-only judge (after rejudge); both: the two on the names both answered")
    c.add_argument("--by-layer", action="store_true", help="also per layer (leaf, middle, top)")
    m = sub.add_parser("compare", help="two results, function by function, with bootstrap intervals")
    m.add_argument("a")
    m.add_argument("b")
    m.add_argument("--repeat-a", type=int, default=None, help="only this repeat of A")
    m.add_argument("--repeat-b", type=int, default=None, help="only this repeat of B")
    m.add_argument("--pass1", choices=("a", "b", "ab"), default="",
                   help="a second-pass result's pass 1 instead of its pass 2, for A, B or both")
    v = sub.add_parser("second", aliases=["revise"],
                       help="a second pass on copies of a finished run's homes, scored pass 2 against pass 1")
    v.add_argument("label")
    v.add_argument("--from", default="E:/mafdet-tools/bench-runs/baseline",
                   help="the finished run's work folder (its <binary>-r<N>/home are copied, never written)")
    v.add_argument("--base-result", default=str(DATA / "walk_bench_baseline.json"),
                   help="its result: model, ctx, server and walk args, and the judge's answers, reused")
    v.add_argument("--base-repeat", type=int, default=1, help="which of its repeats to copy")
    v.add_argument("--work", required=True, help="where the copies go (wiped per binary and repeat)")
    v.add_argument("--pass-args", default="--revise",
                   help='the second pass\'s options for the walk: --pass-args="--revise --revise-no-callers", '
                        '--pass-args="--refine"')
    v.add_argument("--walk-args", default="", help="more options for the walk, after --pass-args")
    v.add_argument("--tree", default=str(ROOT), help="the code tree the walk runs from (its mafdet is imported)")
    v.add_argument("--set", default=str(DEFAULT_SET))
    v.add_argument("--only", default="", help="comma-separated binary names of the set")
    v.add_argument("--repeat", type=int, default=1, help="copies of each home, each with its own second pass")
    v.add_argument("--model", default="", help="default: the base result's")
    v.add_argument("--ctx", type=int, default=0, help="default: the base result's")
    v.add_argument("--port", type=int, default=8079)
    v.add_argument("--url", default="", help="an engine already running (then --model is its alias)")
    v.add_argument("--server", default="", help="default: the base result's")
    v.add_argument("--judge-from", nargs="*", default=[],
                   help="more results whose judged names are reused (another second pass's)")
    v.add_argument("--no-judge", action="store_true")
    v.add_argument("--out", default="")
    j = sub.add_parser("rejudge", help="add the names-only judge to existing results (rewrites them)")
    j.add_argument("results", nargs="+")
    j.add_argument("--names-only", action="store_true", help="the judge reads the real and the given name, no summary")
    j.add_argument("--only-changed", action="store_true",
                   help="second-pass results: only the functions whose name the second pass changed")
    j.add_argument("--dry-run", action="store_true", help="count the names to ask, ask nothing, write nothing")
    j.add_argument("--url", default="http://127.0.0.1:8079", help="the engine (started there if nothing answers)")
    j.add_argument("--model", default="qwen3-14b:IQ4_XS")
    j.add_argument("--ctx", type=int, default=16384)
    j.add_argument("--set", default=str(DEFAULT_SET))
    a = ap.parse_args(argv)
    return {"run": cmd_run, "score": cmd_score, "book": cmd_book, "calibrate": cmd_calibrate,
            "compare": cmd_compare, "second": cmd_second, "revise": cmd_second, "rejudge": cmd_rejudge}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
