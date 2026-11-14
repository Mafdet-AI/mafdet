# MAFDET plan: one straight road (from 2026-11-14)

Agreed with the project's owner on 2026-11-14: the project was spreading over too many fronts. This plan
is binding for every session, human or agent. It says what is worked on, in which order, and what waits.
Change it only when the owner asks; when an item is finished, mark it done here and record its results in
`research/LOG.md`.

## The one idea

MAFDET is **thinking ahead**: the model reads and reasons while the person types, so the wait after Enter
shrinks (Qwen3-4B 12.2 → 3.2 s, Qwen3-14B 108 → 47 s on an RTX 2060; five machines measured). That is what
is new, and it is what we finish, show and get used. Everything else either serves it or waits.

## Track A: finish thinking ahead (weeks 1-2, first)

- **A1. Check the answers.** *(2026-11-14: proxy done, 21/22 with against 22/22 without, twice, and a loop
  guard; problem 19 on the 14B: 3/3 right with and without, the old misses were noise. A1 done. Then, at the
  owner's request, made as right as without it: two branches that must agree, 40/40 on a held-out set, 2.5 s
  against 4.8 s; the default of --think-ahead.)* `mafdet proxy --think-ahead` was measured on speed only ("answers not checked
  yet", PROXY.md). Check that its answers are as right as without it, on the same 24 problems and the Open
  WebUI conversations. Also read problem 19 on the 2060's 14B (LOG "Next" 0): a limit of thinking ahead
  on slow models, or noise?
  *Done when:* accuracy with and without is in the LOG, and in the README if it differs.
- **A2. Write it up.** `research/REPORT.md`: the method (reading ahead, reasoning ahead, the frame, what is
  exact and what is not), one table per machine, the limits (frames per model family, the language cue, the
  split models), how to reproduce each number. From the README's Results and the LOG; no new runs.
  *Done when:* someone who never saw the project can understand it and rerun one number.
- **A3. Used by someone else.** The 5-minute guide (`mafdet/proxy/clients/README.md`) followed by a person
  who is not the author, on Open WebUI with llama.cpp and with Ollama; fix what they trip on.
  *Done when:* one outside person runs it end to end; their problems are fixed or written down.
- **A4. One more model family** (week 3, if A1-A3 are done): the reasoning-ahead frame measured for the
  family people ask for most (Reddit, issues), the same way it was for Qwen3.

## Track B: the agent, through thinking ahead (weeks 2-3, after A1)

The agent is kept only as the place where thinking ahead goes next. No feature that does not serve that.

- **B1. Measure thinking ahead in the agent** *(2026-11-14, on the 2060: first tool earlier in 10, 6, 12 of
  13, answer not; noisy; a render fix found on the way. The 5070 Ti run is left; B2 waits for the owner.)* (built 2026-11-12, LOG "Next" 8, phase 3): 10-15 agent requests
  (read a file and answer, find a function in a binary, search a folder) typed by `research/thinking_ahead.py`'s
  typist; Enter → first tool call and → answer, with and without; whether the notes change the first tool.
  RTX 5070 Ti with the 14B (the reference machine), then the 2060 with the 4B.
  *Done when:* the numbers are in the LOG and the README.
- **B2. Thinking ahead while a tool runs** (the GPU idles during a long command, as it does while typing):
  only if B1 shows a gain. A one-page design in the LOG first, agreed with the owner, then the build.

## Paused: do not work on these, do not propose them

Unless the owner asks for one by name, in this session:
- the walk's second pass, its variants, the chooser, the walk's measurement runs (decided 2026-11-14:
  `--revise` without the draft, no thinking; the walk is good enough for personal use);
- new agent features: cloud providers, Claude, `/model`, interface, tools, phases 4-5 (crackmes, abliterated
  models in the catalog);
- video, promo, mascot, branding;
- engine work: activation sparsity, a rewrite in Rust, kernels, catalog calibration (decode efficiency,
  MoE tuning), more quantization studies;
- proxy steps beyond A3 (vLLM, SGLang, more engines) unless a contributor brings them.

Always fine, whatever the track: fixing bugs, answering issues and outside pull requests (the AMD volunteers,
the proxy's contributors), keeping tests green, keeping the LOG and the README true.

## Rules for every session

1. Before starting, name the plan item the work belongs to. If it belongs to none, say so to the user
   before doing it, and do not grow it beyond what was asked.
2. Measure before building: a "built, not measured" thing is measured or removed, not extended. No new
   feature without a line on how it will be measured.
3. Do not suggest paused work as a next step. When something paused comes up, write it under "Ideas for
   later" below in one line and go back to the plan.
4. When an item is done: mark it here, results in the LOG (and the README if they are final).

## Ideas for later (one line each, not to work on)

- Symbols (public PDBs) for the top of the walk's tree; the model choosing among names with the code in view.
- libllama in-process (or a small llama.cpp fork) if an experiment needs cache and batch control the server
  lacks; a ggml patch for activation sparsity.
