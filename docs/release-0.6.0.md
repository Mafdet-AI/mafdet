<!-- Draft of the GitHub release notes for v0.6.0 (title below). Not published: the owner decides.
     Every number comes from README.md, PROXY.md or research/LOG.md. -->

# MAFDET 0.6.0 — thinking ahead, set up in a minute

**MAFDET makes a local model read and think while you type. 0.6.0 makes the proxy easy to set up and opens it to more engines.** `mafdet proxy` puts MAFDET between your chat front end and the engine: the answer starts as soon as you press Enter. The user script for Open WebUI now installs in one click from the proxy itself, and its status page walks you through the setup.

| Through `mafdet proxy`, Enter → first token | without | with |
|---|---|---|
| Open WebUI, Qwen3-4B on an RTX 2060, 8 conversations each way (mean) | 5.4 s | **0.16 s** |
| Ollama 0.40.15, qwen3:4b on an RTX 5070 Ti, a pasted prompt of ~1,700 tokens | 250 ms | **48 ms** |
| Open WebUI with `--think-ahead`, first word of the answer (mean of 22 turns, against reading ahead only) | 7.6 s | **4.3 s** |

**Thinking ahead's answers:** With `--think-ahead`, 21 of 22 right answers against 22 of 22 without, in each of two runs (22 word problems with one right answer, English, Qwen3-4B on an RTX 2060); the one lost was a different problem each time. A reasoning ahead that goes round in circles is now stopped and dropped on Enter. `--think-ahead` stays off unless you ask for it.

## Get started

```bash
pipx install mafdet            # or: pipx upgrade mafdet · Windows with python.org's Python: pip install -U mafdet
mafdet doctor --install        # llama.cpp for this machine, if you have not installed it yet
mafdet models --download qwen3-4b
mafdet proxy --model qwen3-4b
```

Then open **http://127.0.0.1:8077/proxy**: it says what to add in Open WebUI (an OpenAI connection to `http://127.0.0.1:8077/v1`) and has a button that installs the user script in Tampermonkey. Every step, with Docker, Ollama and your own llama-server: [Open WebUI + MAFDET in 5 minutes](https://github.com/Mafdet-AI/mafdet/blob/main/mafdet/proxy/clients/README.md).

## What's new

- **The user script in one click.** The proxy serves it at `/proxy/open-webui.user.js`, already pointed at itself and at the addresses it accepts (`--cors`); Tampermonkey offers to install it and checks that link for newer versions. No more copying a file into the editor.
- **A setup guide on the status page.** `/proxy` shows the connection to add and the script's install button until the first draft arrives, then whether it works: the time to the first token, how much of the prompt was already read on Enter, the side requests sent to their own slot. `/proxy/stats` has the same as JSON.
- **More engines** with `mafdet proxy --url`: Ollama, LM Studio, vLLM, SGLang and any OpenAI-compatible server, through one adapter that asks the server what it is. On Ollama, run for real, the drafts go through its OpenAI route, which reports the reuse exactly (`--engine` to say which engine it is, `--api-key` where one is needed).
- **Docker**: `docker/docker-compose.yml` runs llama.cpp, the proxy and Open WebUI together (written to the options measured on Windows, not yet run in Docker: issues welcome).
- **Thinking ahead in the proxy**: Enter now stops the reasoning chunk in flight at its next token, instead of waiting for it to end.
- **The app**: delete a model from the app, the disk space shown, the answer written ahead shown live under the message, a mark where Enter fell in the reasoning, and a thinking budget after Enter (`--think-budget`, Settings).
- **`mafdet agent`, experimental**: a local model working with tools (files, a shell that stays open, MCP servers, the web), first for reverse engineering, in a full-screen terminal conversation. It thinks ahead while you write the request (Qwen3-4B on an RTX 2060, one request: the first tool call 0.85-1.75 s after Enter against 3.5-5.5 s without). `--walk` names every function of a binary from the leaves up into a book of its own; `--revise` names them all again with their callers and a summary of the program. `/model` switches models during a session, local or in the cloud (`--provider`).
- **The walk's defaults** (`mafdet agent --walk`): no thinking unless `--think` (as good names in a sixth of the time, measured on six Windows tools); `--revise` names the functions again without showing the model its first names (shown them, it gives them back); `--refine` is no longer offered.
- **The catalog** estimates the prompt's reading speed on Ampere and Blackwell cards from measurements on them (RTX 3060, RTX 5070 Ti).

Thanks to [dariodd](https://github.com/dariodd) for the exact reuse on Ollama (#6) and the RTX 5070 Ti measurements (#7).

## Tested on

- Windows 11, RTX 2060 6 GB, llama.cpp b11747 (CUDA), Qwen3-4B Q4_K_M, Open WebUI 0.13.0 in Chrome with Tampermonkey.
- Windows 11, RTX 5070 Ti 16 GB, Ollama 0.40.15, qwen3:4b (the proxy in front of Ollama).
- The proxy's tests run without a GPU, against fake engines (llama.cpp, vLLM, Ollama).

## Known limitations

- **Thinking ahead is experimental**: Qwen3 only (each model family needs its own frame, measured).
- **vLLM, SGLang and LM Studio** are written against their documented APIs and checked on fakes, not yet run for real: if you run one, the `engine` line the proxy prints and `/proxy/stats` in an issue fill its row ([PROXY.md](https://github.com/Mafdet-AI/mafdet/blob/main/PROXY.md), "More engines").
- **Docker**: the compose file has not been run in Docker yet.
- **Memory**: Open WebUI takes a few GB of RAM on top of llama-server; on a 16 GB PC close the heavy apps first.
- **Not read ahead:** files and knowledge attached in Open WebUI (its RAG template changes the prompt), and long pastes when Open WebUI's "Paste Large Text as File" is on.
- Answers can differ in wording, rarely in substance, from a run without the proxy: llama.cpp computes a prefix read ahead in other batches. `--exact` avoids it.
- **The agent** has run with Qwen3-14B on an RTX 4070 SUPER; models in the cloud are tested against fake providers only, not yet with a real key on a task.

**Full changelog:** https://github.com/Mafdet-AI/mafdet/compare/v0.5.0...v0.6.0
