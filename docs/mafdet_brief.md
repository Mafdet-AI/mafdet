# Mafdet Agent: factual brief for the promo

Sources are `README.md` lines 155-213 ("An agent with tools"), `mafdet/agent/*.py` and `research/LOG.md`. The README labels the feature **experimental**.

## 1. In one sentence
`mafdet agent` puts a local model to work on your machine in a loop. It reads and writes files, runs commands and uses the tools of MCP servers such as a decompiler, until the task is done. It was built first for reverse engineering, where the binary should not leave the machine (README 157; `mafdet/agent/__init__.py`).

## 2. Key concepts
- **Tool loop**: the model asks for tools and reads their results until it answers without asking for one. Bad JSON, unknown tools and declined calls are told back to the model (`loop.py`, max 40 steps).
- **Thinking ahead**: while you type, the model reasons inside the real conversation. On Enter, the first step starts from those notes. This needs llama-server and a reasoning model such as Qwen3 (`ahead.py`, `tui.py`).
- **Approvals ask → edits → all**: reads inside the working folder run on their own. Writes, the shell and MCP tools not marked read-only ask first. Anything outside the folder always asks, and installs ask in every mode (`permissions.py`).
- **Context budget**: tool outputs are cut to 4,000 characters and kept whole on disk. Old outputs are left out down to 60% of the budget, then the conversation is compacted. Notes and the to-do list are never left out (`context.py`, `memory.py`, `loop.py`).
- **MCP + `find_tools`**: a profile of 16 tools starts on, and the model finds and turns on the rest itself (`mcp.py`).
- **`--walk`**: code, not the model, names every function of a binary from the leaves up, giving the model one function at a time (`walk.py`).
- Also: `/model` switches between a local llama-server model and a cloud provider (`switch.py`, `cloud.py`, `claude.py`). `need_tool` installs only from a pinned, digest-checked catalog (`kit.py`).

## 3. Execution flow
1. **TYPE**: each draft is rendered 0.15 s after the last keystroke, and the model writes notes in 32-token chunks (`ahead.py:24`, LOG 308-309).
2. **ENTER**: the first step answers from the notes (`AheadModel.chat`).
3. **FIT CONTEXT**: old outputs are left out, or the conversation is compacted (`loop.py:_fit`).
4. **MODEL STEP**: reasoning, text and tool calls stream in (`model.chat`).
5. **CHECK**: arguments, whether the tool exists and the approval (`loop.py:_call`, `permissions.allowed`).
6. **RUN TOOL**: the output is clipped and saved, then the loop goes **back to FIT CONTEXT**.
7. **ANSWER**: a step with no tool calls ends the turn (`loop.py:233-237`).

## 4. Snippets (≤46 chars per line)
**A. The loop's core**, `mafdet/agent/loop.py` 233, 237-241. Remove 12 leading spaces and replace 234-236 with `...`:
```
if not reply.tool_calls:
    ...
    return reply.content
for call in reply.tool_calls:
    if self.stop.is_set():
        raise Stopped
    self._call(call)
```
**B. Approvals**, `permissions.py:20`:
```
MODES = ("ask", "edits", "all")
```
In the TUI, Shift+Tab cycles `ask -> edits -> all` (`tui.py:18`).

**C. Status lines**, from `tui.py` 421-428 and 398, with the README's own example at line 176:
```
=^.^= Making biscuits…
 · thinking ahead 273 tok
```
273 is a measured value (LOG 318). The full activity line also shows `(N s · N tokens · esc to stop)`. If shown, cut it after "biscuits…" or use placeholders.

**D. Real commands**, README 162 and 169:
```
mafdet agent --mcp rea --profile native-static
mafdet agent --walk C:\samples\crackme.exe
  --mcp rea --max-functions 50
```
The first line is exactly 46 characters. The walk command is one line in the README; split it as shown.

**E. Self-correction messages**: `mcp.py:321` and `loop.py:268`:
```
{name}: on, call it from the next step
The user declined this call. Do not
repeat it: go on another way,
```

## 5. Measured numbers
- **Thinking ahead**: Qwen3-4B on an RTX 2060, 16k context, one request typed at about 3 words/s, the same task 3 times. The model reasoned 273-294 tokens ahead.
  - With notes, the first tool call came 0.85-1.75 s after Enter and the turn took 4.0-5.1 s.
  - Without notes, the first tool call took 3.5-5.5 s and the turn 6.3-8.3 s.
  - Every answer was right.
  - LOG 316-321 calls this "a direction, not a result". The README (207) says "0.95-1.75 s … against 5.5 s", which differs from the LOG. Use the LOG figures or say "under 2 s vs up to 5.5 s".
- **Tool schemas**: the 8 built-in tools take 891 tokens, and the whole first request takes 1,305 (README 195; LOG 562). The `native-static` profile (16 tools + `find_tools`) takes 1,696 tokens; it took 3.8k with schemas only slimmed. The rea server lists 207 tools with 3.4 MB of schemas (LOG 631).
- **Real sessions**: Qwen3-14B on an RTX 4070 SUPER made about 60 tool calls, none malformed. Reading and explaining a file took 2 steps (24 s); fixing a bug took 5 steps (81 s) (README 213; LOG 503-508).
- **Compaction**: in a 6k context, 4,496 → 2,359 tokens, and the task finished right (LOG 528-529).
- **Walk**: where.exe, 86 of 86 functions named, 0 failed, about 25 min (about 17 s a function), on the same card at 16k (README 213; LOG 186-188, 395-406).
- **need_tool**: Node.js + chrome-devtools-mcp downloaded, verified and installed in 18 s (LOG 349-350).

## 6. Do not say
- That cloud providers or Claude were used for real. They are "not yet tried with a key on a real task" (README 209).
- That a second pass improves names at the top of the tree (main and the like). `--revise` names the leaves and the runtime functions better; `--refine` changed nothing (measured 2026-11-13, LOG).
- That the model running commands, background jobs or the web were tested with a real model. They were tested only with a scripted model (README 213).
- That the model installs tools by itself in real use. Not tried, and neither were Ghidra or Linux/macOS installs (LOG 351-353).
- That the walk names functions accurately. One reader judged 23 functions: 10 right, 8 vague, 5 wrong, with main misnamed. The README calls this "not a measurement".
- That thinking ahead is exact, works with every model, or works in the cloud. It is not exact, needs llama-server plus a reasoning model, and is off with providers.
- That it is fast on 6 GB. Only a 4B model is fast enough for a loop there, and the reference card has 12-16 GB (README 174).
- That it is a sandbox. Running a binary should be done in a VM (README 200), and the shell is not blocked from installing; that is "a prompt rule, not a guard" (LOG 347-348).
- That it is "4x faster", "production-ready" or better than other agents. None of this is measured.
