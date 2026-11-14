# Thinking ahead: a technical report

MAFDET, 2026-11-14. This report gathers what has been measured so far on one idea: **a local LLM can use
the time a person spends typing**. It reads the message while it is written (reading ahead), and a reasoning
model also starts reasoning about it before Enter (thinking ahead). It is written for someone who has never
seen the project. Every number below comes from the repository's README, `PROXY.md`, the proxy's guide
(`mafdet/proxy/clients/README.md`) or the research log (`research/LOG.md`), where each run is described in
full with its data file under `research/data/`.

## 1. The problem

A chat interface makes the model wait for Enter before it does anything. While the person types, the GPU
sits idle. On Enter the model first has to *read* the whole prompt (prefill, fast and parallel), then a
reasoning model such as Qwen3 in thinking mode *thinks* for hundreds of tokens before the first word of
its answer (decode, one token at a time). On a mid-range GPU that thinking is the longest wait: ten seconds
and more on a word problem with Qwen3-4B on an RTX 2060, and about two minutes on Qwen3-14B when the model
does not fit in VRAM.

The time spent typing is long enough to do much of that work. A message typed in 30 seconds leaves room
for most of the 4B's reasoning on an RTX 2060 (83 tok/s), and about 160 tokens of the 14B (5 tok/s, partly
on the CPU).

## 2. The method

### 2.1 Reading ahead (exact)

While the model reads, it stores intermediate results for every token in the KV cache. Those results
never change for a given prefix, so they can be computed before Enter. MAFDET keeps the cache in sync with
the text while it is typed:

1. On every pause it tokenizes the current text.
2. It holds back the last few tokens: a word still being typed can change how the text splits into tokens.
3. It finds the longest common prefix between the new tokens and those already in cache, trims the cache
   there and prefills only what is new.
4. On Enter only a handful of tokens are left to read.

Backspaces, edits in the middle, pastes or missing punctuation do not matter: the common-prefix check
always brings the cache back to exactly what a computation from scratch would produce. The heuristics
(hold-back, debounce) only change how much work is wasted. `mafdet judge` checks this against a from-scratch
computation and must end with 0 errors (on the RTX 5070 Ti: 20 of 20 ok, 0 errors, and on every one of the
556 requests llama-server reused exactly the common prefix).

Reading ahead does not change the prompt, so the answer is the same in principle. In practice, with greedy
decoding, llama.cpp computes a prefix read ahead in other batches than a cold prefill, and that can change
the wording and, rarely, a token that matters (section 4.3). The proxy's `--exact` gives bit-identical
answers at the price of all of the gain.

### 2.2 Thinking ahead (not exact)

Reading ahead takes the prefill out of the wait; the reasoning is still there. So the model also reasons
while the person types (`mafdet strike`, on by default for Qwen3 in the app):

1. **While typing**, the model reasons in chunks of 32 tokens on the message as it is at that moment, on
   whole words (after a space or punctuation, or at a pause).
2. **When the text changes**, the reasoning is not thrown away. It stays in the prompt as notes after the
   new version of the message. Moving it there only costs a prefill (about 60 times faster than writing the
   same tokens again, for the 14B on the RTX 2060). A new line of the notes names the words just typed.
3. **On Enter**, a last sentence tells the model the message is complete. It finishes reasoning from where
   it got to and answers.

The notes are framed as a list of *material for the answer*: "The user is still typing. I will not wonder
what they will ask, nor remark that the text is unfinished. While I wait, I collect material for the
answer: for each key term, name or number already written, one line with what it is and the facts I know
about it, or a partial result." Then one example line from a different message, and for each new piece of
text a line such as `- New words "finestra di contesto": `.

The frame matters. On six messages typed in real time, the share of sentences in the notes about the user
or the message itself (instead of its content) went from 86% with a generic frame to 41-54% with this list,
and to 17-26% with the example line and a logit bias that, only while reasoning ahead, makes words such as
*user*, *incomplete*, *typing* less likely. On the 24 problems of section 3, a generic frame gave 22/24
correct and a median wait of 4.4 s; no frame gave 22/24, 6.9 s and 70% more reasoning; the frame above gave
24/24 and 2.9 s.

Thinking ahead is **not exact**: the model reasons on versions of the message that are not final, so the
answer can differ from the one it would give from scratch. That is why it is always measured on problems
with a known answer, and accuracy is reported next to the wait.

### 2.3 Where it runs

- **The app** (`mafdet`, window and terminal) on llama.cpp: reading and thinking ahead in its own chat.
- **The proxy** (`mafdet proxy`): an OpenAI-compatible server in front of llama-server (or Ollama, vLLM,
  SGLang, LM Studio with `--url`). A front end posts the text being typed to `/v1/drafts`; for Open WebUI a
  small user script does it. `--think-ahead` adds thinking ahead on llama-server (it needs the raw
  `/completion` route).
- **The agent** (`mafdet agent`, experimental): in its full-screen interface the model reasons about the
  request inside the real conversation (system prompt, tools, history) while it is typed.

## 3. Results in the app: reasoning ahead, per machine

Unless stated: llama.cpp b11747, Q4_K_M models, `research/thinking_ahead.py`, 24 word problems with a known
numeric answer, typed in Italian in real time, Qwen3 in thinking mode, "without" is `--ahead 0` and "with"
is the default reasoning ahead. *Wait* is from Enter to the first word of the answer (the reasoning after
Enter included). *Left after Enter* is the reasoning still to write after Enter.

### 3.1 RTX 2060 6 GB (the reference machine)

i5-10400, 16 GB DDR4, Windows 11. Qwen3-4B fits in VRAM (83 tok/s); Qwen3-14B does not (about 4.5-5 tok/s
through llama-server, most of it on the CPU).

| Qwen3-4B, 24 problems | Without | With reasoning ahead |
|---|---|---|
| Correct answers | 24/24 | 24/24 |
| Median wait | 10.7 s | **2.9 s** |
| Mean wait | 12.2 s | **3.2 s** |
| Faster | | in 24 of 24 |
| Reasoning in total | 944 tokens | 662 tokens |
| Left after Enter | 944 tokens | 244 tokens |

Qwen3-14B, 6 problems (0, 4, 6, 13, 19, 23), 6/6 correct both ways: mean wait **108 s → 47 s**, with 164
reasoning tokens written before Enter on average. Single runs vary a lot on this model (the same problem
took 106 s once and 154 s another time): 6 problems give the order of magnitude.

### 3.2 MacBook Pro M2 Pro 16 GB

macOS 26.2, llama.cpp with Metal; Metal may use about 12 GB of the unified memory, so the 14B runs all on
the GPU (18 tok/s). The 4B writes at 54 tok/s.

| M2 Pro | Without | With reasoning ahead |
|---|---|---|
| Qwen3-4B, 24 problems: correct | 24/24 | 24/24 |
| Median / mean wait | 16.7 / 18.1 s | **4.1 / 5.6 s** |
| Faster | | in 24 of 24 |
| Reasoning in total / left after Enter | 966 / 966 tokens | 773 / 248 tokens |
| Qwen3-14B, 6 problems: correct | 6/6 | 6/6 |
| Median / mean wait | 26.5 / 32.7 s | **11.7 / 12.1 s** |

The column without comes from an earlier run of the same day, in which one answer came out empty (the
model ended its turn inside its reasoning, since fixed); the column with is the run after the fix. The Mac
reached thermal pressure *Heavy* for a few minutes (54 → 51 tok/s).

### 3.3 RTX 4070 SUPER 12 GB

i5-14600KF, 32 GB, Windows 11. Both models all on the GPU. Only runs *with* reasoning ahead were made here
(they compared quantizations and settings, not ahead against no ahead):

| RTX 4070 SUPER, with reasoning ahead | Correct | Mean / median wait | Speed |
|---|---|---|---|
| Qwen3-4B Q4_K_M, 24 problems | 24/24 | 2.83 / 2.14 s | 106-119 tok/s |
| Qwen3-14B Q4_K_M, 24 problems, `--warm-up --max-tokens 1800` | 24/24 | 5.65 / 5.03 s | 40-45 tok/s |
| Qwen3-14B IQ4_XS, same | 24/24 | 6.03 / 4.98 s | 43-47 tok/s |

This card was also used to simulate the 2060's 6 GB budget for the 14B (`-fitt 7100`, 15/41 layers on the
GPU): with reasoning ahead, 24 problems, the Q4_K_M split by layers waited 34.0 s on average, the IQ4_XS
with a q8_0 KV cache **21.1 s** (24/24 both). That is why the app now picks IQ4_XS and a q8_0 cache for a
model split with the CPU.

### 3.4 RTX 3060 12 GB

i5-12400F, 16 GB DDR4, Windows 11. `--warm-up`, `--max-tokens 1800` for the 14B and the 30B (3000 for the 4B).

| RTX 3060 | Correct, without → with | Mean wait | Median wait | Faster |
|---|---|---|---|---|
| Qwen3-4B | 24/24 → 24/24 | 10.25 → **3.29 s** | 8.78 → 2.26 s | 24 of 24 |
| Qwen3-14B, all on the GPU | 23/24 → 24/24 | 20.19 → **7.03 s** | 19.00 → 6.32 s | 23 of 24 |
| Qwen3-30B-A3B (MoE, experts partly in RAM, `-ncmoe 20`, q8_0 KV) | 23/24 → 24/24 | 18.94 → **7.33 s** | 17.76 → 6.99 s | 23 of 24 |

Both misses without are problem 16 (the snail) cut at 1800 tokens with no answer, not a wrong number.
Reasoning ahead works on the MoE as on the dense models.

### 3.5 RTX 5070 Ti 16 GB

Ryzen 7 7700, 32 GB DDR5, Windows 11, CUDA 13.4. Typed at 8 characters a second.

| RTX 5070 Ti | Correct, without → with | Mean wait | Median wait | Speed | Faster |
|---|---|---|---|---|---|
| Qwen3-4B, `--max-tokens 3000` | 24/24 → 24/24 | 5.39 → **1.73 s** | 4.92 → 1.32 s | 178 tok/s | 23 of 24 |
| Qwen3-14B, `--warm-up --max-tokens 1800` | 21/24 → 24/24 | 8.95 → **3.49 s** | 8.31 → 2.88 s | 75 tok/s | 22 of 22 |

On the 14B the three misses without are reasoning cut at 1800 tokens (problems 9, 16, 17); with reasoning
ahead nothing is cut, because the reasoning done while typing brings them inside the budget. On the 4B the
one slower problem is again the snail (7.80 → 9.48 s, 2617 reasoning tokens instead of 1366).

**A fast card gathers more, not less.** On the 2060 reasoning ahead cut the reasoning in total (944 → 662
tokens); here it raises it (943 → 1286 on the 4B): typing takes the same seconds, and a card writing 178
tok/s covers 1007 tokens before Enter against 418. What is left after Enter, which is what the wait is made
of, is about the 2060's (280 tokens against 244).

### 3.6 Radeon RX 5700 XT 8 GB (one volunteer's run)

Sent by a volunteer (issue #1): Ryzen 5 3600, 16 GB, Windows 11, llama.cpp b11747 with Vulkan.

| RX 5700 XT, Qwen3-4B, 24 problems | Without | With reasoning ahead |
|---|---|---|
| Correct | 24/24 | 24/24 |
| Median / mean wait | 10.9 / 14.0 s | **6.8 / 7.7 s** |
| Faster | | in 22 of 24 |
| Reasoning in total / left after Enter | 920 / 920 tokens | 595 / 252 tokens |

It works, but the wait with reasoning ahead is twice the 2060's with about the same reasoning left. The
card reads at only **93 tok/s** (the 2060 about 2,000). The notes come after the message in the prompt,
so the last words typed make the model read them again after Enter: 343 tokens, 0.17 s on the 2060, about
3.7 s on this card, which accounts for the difference. Whether 93 tok/s is llama.cpp's Vulkan on RDNA 1
with AMD's Windows driver, or something in MAFDET, is not known yet.

### 3.7 Related results in the app

- **Exact answer ahead** (no reasoning, a pause before Enter, text unchanged): `strike` also writes the
  answer itself, which holds only for that exact text. Qwen3-4B, RTX 2060: a 300-token answer, 200 written
  before Enter, on screen 1.2 s after Enter against 3.5 s; a 168-token answer, all of it written ahead, on
  screen at once against 1.9 s. With greedy decoding it is identical token by token to the answer started
  after Enter.
- **Guessing the ending** (`--branches 1`, experimental): the model guesses how the message ends and reasons
  on the guess in a parallel slot, adopted only on an exact guess. Qwen3-4B, 2 runs × 24 problems: mean wait
  4.17 → 3.51 s, slowest tenth 7.9 → 5.3 s, 48/48 both, 14 of 48 guessed exactly; not significant (faster
  on 16 of 24, sign test p = 0.15).
- **A small model gathering for a large one** (`--gatherer`): no gain on the RTX 2060. Reasoning ahead by
  itself gave the 14B 47 s; an untrained Qwen3-0.6B gatherer 60 s, a fine-tuned one 79-86 s, ideal
  hand-written material 62 s (49 s when presented as the model's own reasoning).

## 4. Results through the proxy

### 4.1 Open WebUI

Open WebUI 0.13 on an RTX 2060, Qwen3-4B, 8 conversations of 2-3 messages each way (22 counted turns),
typed at 12 characters a second, driven as its browser frontend does (`research/proxy_openwebui.py`):

| Enter → | mean | median | slowest |
|---|---|---|---|
| first word, thinking off: llama-server with its default options | 5.4 s | 3.3 s | 20.7 s |
| first word, thinking off: through `mafdet proxy` | **0.16 s** | **0.14 s** | 0.4 s |
| first word of the answer, thinking on: `mafdet proxy` | 7.55 s | 6.34 s | 21.0 s |
| first word of the answer, thinking on: `mafdet proxy --think-ahead` | **4.31 s** | **4.06 s** | 10.4 s |

Most of the wait without the proxy is not reading. Open WebUI asks the model for a title, tags and
follow-up suggestions around every answer, and the message queues behind them; it also sends its 30
built-in tools (~5.8k tokens) with every request. The proxy sends those side requests to a second slot,
keeps the conversation in the first, and reads the message while it is typed: through it every turn had 1
token left to read on Enter. With `--think-ahead` the turn was faster in 20 of 22 turns.

Answer accuracy with --think-ahead in the proxy: being measured (A1).

### 4.2 Ollama

Ollama 0.40.15, qwen3:4b, RTX 5070 Ti, `mafdet proxy --url`: Enter → first token on a pasted prompt of ~1,700
tokens **250 → 48 ms**, on short typed messages 39 → 27 ms mean. On Ollama the drafts go through its OpenAI
route, which reports the cached tokens exactly. The final numbers agreed in 5 of 5 comparable rows; Ollama
itself is not bit-stable between two identical requests, proxy or no proxy. `--think-ahead` is not available
there (no raw completion endpoint): reading ahead only.

### 4.3 The proxy's own bench (llama.cpp, RTX 2060)

`mafdet/proxy/bench.py`, Qwen3-4B, greedy, thinking off, Enter → first token without → with the proxy:

- 24 typed problems, 12 characters a second, Enter 0.35 s after the last key: **302 → 102 ms** mean, faster
  in 24/24. Much of it is the GPU's clocks: idle while the person types, it slows down, and the drafts keep it
  awake.
- 4 pasted prompts (~2k and ~4k tokens), Enter 2 s after the paste: **1365 → 54 ms**; Enter 0.35 s after the
  paste: 1171 → 880 ms (only the time before Enter can be hidden).
- Answers: 26 of 27 comparable agree on the final number. The one that does not (the printer problem, 133
  without, 132 with the proxy) is llama.cpp computing the read-ahead prefix in other batches; the same
  problem flipped on a second card. `--exact` removes it.

## 5. In the agent: a first run

`mafdet agent`, Qwen3-4B all on the RTX 2060, 16k context, one request typed at about 3 words a second
("read notes.txt and tell me who owns the installer and how much budget is left"), the same task three
times: with notes (273-294 tokens reasoned ahead) the first tool call came 0.85-1.75 s after Enter and the
turn ended in 4.0-5.1 s; without, 3.5-5.5 s and 6.3-8.3 s; every answer right. Single samples on one task: a
direction, not a result. The agent needs its own frame: the chat's frame made the 4B list decades of history
from "read notes.txt and"; a frame about preparing the work ("what the words point to among files,
programs, addresses, tools; what to find out; the first step") gave on-task notes ("I will call
read_file"). Measuring it on a set of agent requests is the next step (plan item B1).

## 6. Limits

- **One model family.** The frame is written and measured for Qwen3 (dense 4B, 8B, 14B and the 30B-A3B MoE).
  Every other reasoning model, especially those with their own thinking formats, needs its frame measured.
- **Language.** The frame's example line is Italian, and all benchmarks are Italian. On the M2 Pro one of 3
  runs of an English word problem got a right answer in Italian after reasoning ahead; a check with 3
  English problems found 0 of 12 Italian answers, so it is rare. A sentence asking the model to answer in
  the message's language removed it (0 of 24) but made the reasoning longer (4B mean 5.6 → 7.2 s, 14B 12.1 →
  22.7 s on the Mac), so it was not kept.
- **Models split between GPU and CPU.** The CPU's layers compute every token they are given, so anything
  that processes several tokens at once costs the 14B on the 2060 almost proportionally: 2 sequences run at
  77% of the speed of one each, and a 9-token speculative draft costs 2.4 decoding steps. Parallel branches
  and speculative decoding therefore do not help there; the 14B also writes only about 160 tokens before
  Enter in a 30 s message, against about 200 left after Enter.
- **Problem 19 on the 2060's 14B.** "30 vehicles, 100 wheels, how many motorbikes" (answer 10) was wrong
  with reasoning ahead in 2 of 2 runs on the RTX 2060's 14B (one per quantization), with numbers that are not
  in the text, and right without it twice; it is right with reasoning ahead on the 4070 SUPER, the 3060 and
  the M2 Pro. Whether it is a limit of reasoning ahead on slow models or noise is open: the reasoning of
  that problem has not been saved and read yet.
- **Single runs and small sets.** Two runs of the same 24 problems on the same machine differed by about 1 s
  in mean wait (3.2 s and 4.17 s on the 2060's 4B); the 14B runs use 6 problems. The tables give orders of
  magnitude, not precise effects.
- **Answers in the proxy.** `--think-ahead` in the proxy has been measured for speed; whether its answers
  stay right is being checked (plan item A1).
- **Exactness.** Reading ahead can change a greedy answer's wording, and rarely a number, through llama.cpp's
  batching (`--exact` avoids it); thinking ahead is not exact by design.

## 7. Related work

Several lines of research share the idea of letting a model start before its input is complete:
simultaneous inference on a prompt that is still arriving, models that think while reading a stream, spoken
language models that think while still hearing the user, and training-free asynchronous reasoning. The
README lists the papers the project knows of (section "Related research"). MAFDET's part is practical: it
works on unmodified open models in llama.cpp, on consumer GPUs, inside the chat front ends people already
use, and it reports accuracy next to every wait.

## 8. How to reproduce

From the source (`git clone https://github.com/Mafdet-AI/mafdet.git`, `pip install -e .` in a virtual
environment), with llama.cpp installed by `mafdet doctor --install` and the model downloaded by the app or
`mafdet models --download qwen3-4b`. Leave the machine otherwise idle; on a laptop, check its temperature.

**The 4B table (section 3, any machine)**, about 20 minutes:

    python research/thinking_ahead.py research/data/my_machine_4b.json

It types the 24 problems in real time with and without reasoning ahead and writes one row per problem
(answer, correct or not, wait, reasoning tokens ahead and after Enter). Runs longer than about ten minutes
are better split with `--problems 0:12`, then `--problems 12:24 --append`.

**The 14B rows:**

    python research/thinking_ahead.py out.json --gguf Qwen/Qwen3-14B-GGUF:Qwen3-14B-Q4_K_M.gguf \
        --max-tokens 1800 --warm-up --problems 0,4,6,13,19,23 --config "reason ahead="

(the 6-problem set of the RTX 2060 and the Mac; drop `--problems` for all 24, as on the 3060 and the 5070 Ti;
`--config "no ahead=--ahead 0"` adds the column without).

**Guessing the ending:** `python research/thinking_ahead.py out.json --config "branches 1=--branches 1"`.

**The proxy's bench (section 4.3):** start the engine with `mafdet proxy --model qwen3-4b` (llama-server on
port 8078), then

    python -m mafdet.proxy.bench --url http://127.0.0.1:8078 --model qwen3-4b --prompts 24 \
        --typing-cps 12 --long 4 --proxy-port 8079

**Open WebUI (section 4.1):** `mafdet proxy --model qwen3-4b`, Open WebUI with two connections (one through
the proxy, one straight to llama-server), then, with Open WebUI's Python:

    python research/proxy_openwebui.py --arm mafdet --warmup 1 --out research/data/x.json
    python research/proxy_openwebui.py --arm direct --warmup 1 --out research/data/y.json

(add `--think` for thinking on; the setup is in the script's docstring and in
`mafdet/proxy/clients/README.md`).

**Correctness of the cache:** `mafdet judge --backend llama` must end with 0 errors.

The data of every row quoted here is in `research/data/` (for example `thinking_ahead_4b_5070ti.json`,
`thinking_ahead_14b_3060.json`, `thinking_ahead_4b_rx5700xt.json`, `proxy_openwebui_think_4b_2060.json`),
and `research/LOG.md` describes each run, what was found on the way and what is still open.
