# MAFDET AGENT: phonk edit storyboard

> **Revision 2 (2026-11-13).** MAFDET agent's own colours (amber phosphor, `contracts.ts`), the real window at work
> and the cheetah mascot, and a theatrical close on the slogan. What changed against the table below:
> - **S04 (16-20)**: the real window slams in on the drop under MAFDET / AGENT; the camera goes into its banner on 18
>   (THE CHEETAH) and the cheetah taps its ball on 19 (HUNTS AHEAD).
> - **S07_Live (36-40)** replaces S07_SelfCorrect: a whole turn in the real window, one event a beat
>   (WATCH IT WORK, READS, EDITS, ANSWERS).
> - **S08 (40-48)**: the request typed in the real window while its status line counts `thinking ahead` up to 273;
>   the camera goes into that count in the break; ENTER on 47.
> - **S10 (52-60)**: Shift+Tab through ask, edits, all on the real status line, one a beat; then the approval
>   prompt of a shell command, y on 58, it runs.
> - **S14_Eyes (78-80)** replaces S14_Recap: black, the cheetah's eyes open and burn, three black frames.
> - **S15 (80-88)**: the cheetah slams in, the identity's wordmark uncovers, letterbox and push, black but the eyes,
>   AHEAD, (84) TOGETHER. (85), the final card; everything fades but the eyes, which go out last.
> The live clips are recorded from the real interface by `video/tools/capture_tui.py` with a scripted model and real
> tools; every one says so in its caption.


Vertical 1080x1920, 30 fps, 1347 frames (44.9 s), 88 beats = 22 bars of 4, one beat = 15.3 frames.
The only source of facts is `docs/mafdet_brief.md`. Every on-screen claim is mapped to it in section 5.

## 1. Concept and arc

**Concept.** A black screen, white Anton type and one blood-red accent. The video asks why a binary should
be sent anywhere, then shows the answer as a machine being assembled in time with the track: a local model
that thinks while you type, a loop that checks every tool call, and the numbers that were measured. Nothing
is decorated. Type slams, a diagram builds one box per beat, real code and real terminal lines sit still long
enough to be read, and every number carries its condition in a small mono caption.

**Arc.** HOOK (bar 0): a question. BUILD (bars 1-3): what Mafdet Agent is, ending on a bare terminal while
the bass is out. DROP (bars 4-11): the name, the 7-step flow built node by node, the loop going back, the
loop's real code, self-correction, then thinking ahead while a request is typed. The break (beats 45-46)
is the silence of the model thinking, and the pickup (beat 47) is the Enter key. RELAUNCH (bar 12): the
measured number. Then approvals, context, MCP tools and `--walk`. FINAL HIT (bar 20): MAFDET AGENT, the
command, one line: STOP WAITING. START TYPING.

## 2. Conventions for both programmers

- **Frames.** `f(b) = Math.round(beats[b] * 30)` from `video/src/beats.json`. A half beat `b.5` is
  `Math.round((beats[b] + beats[b+1]) / 2 * 30)`. A scene is a `<Sequence from={f(fromBeat)}>` lasting
  `f(toBeat) - f(fromBeat)` frames. Inside a scene every `at` is `f(b) - f(fromBeat)`. Never hard-code 15.
- **Scene boundaries in frames:** b0=0, b4=61, b12=184, b16=245, b20=306, b32=490, b36=551, b40=612,
  b48=735, b52=796, b60=918, b64=980, b72=1102, b78=1194, b80=1225, end=1347.
- **Safe area:** x 70-1010, y 220-1500 (`SAFE`). Zones used below: **TOP** y 220-560, **MIDDLE** y 560-1160,
  **BOTTOM** y 1160-1500. Mono captions sit at the lower edge of their zone, JetBrains Mono 30 px,
  `COLORS.dim`, never upper-cased, never animated beyond a 3-frame fade.
- **Main text** is `PunchText` (Anton, upper case), at most 4 words on screen at once. A new main text
  replaces the previous one on its beat (hard cut, no cross-fade) unless the list says "stays".
- **Colors:** `COLORS.black` background, `COLORS.white` text, `COLORS.accent` only where the list says
  "accent". No other color.
- **Hits.** "shake" = the beat's frame goes in `ShakeOnBeat.hits`. "flash" = `FlashCut` of 2 frames, white.
  "glitch" = `GlitchTransition` of 5 frames at that beat. Defaults unless a strength is given.
- **Grain.** `GrainOverlay` is applied once at the root by the orchestrator. Scenes do not add it.
- **Do not show:** the `=^.^=` face, any emoji or mascot, the words "fastest", "4x", "production-ready",
  "sandbox", "accurate".
- Each scene file exports a component of the same name as the file: `export const S01_Hook: React.FC`.

## 3. Scene table

| id | file | beats (end exclusive) | bars | section | block |
|---|---|---|---|---|---|
| S01 | `video/src/scenes/S01_Hook.tsx` | 0-4 | 0 | hook | A |
| S02 | `video/src/scenes/S02_WhatItIs.tsx` | 4-12 | 1-2 | build | A |
| S03 | `video/src/scenes/S03_Command.tsx` | 12-16 | 3 | build (bass out, pickup) | A |
| S04 | `video/src/scenes/S04_Drop.tsx` | 16-20 | 4 | DROP | A |
| S05 | `video/src/scenes/S05_Flow.tsx` | 20-32 | 5-7 | drop | A |
| S06 | `video/src/scenes/S06_LoopCode.tsx` | 32-36 | 8 | drop | A |
| S07 | `video/src/scenes/S07_SelfCorrect.tsx` | 36-40 | 9 | drop | A |
| S08 | `video/src/scenes/S08_ThinkAhead.tsx` | 40-48 | 10-11 | drop (break, pickup) | A |
| S09 | `video/src/scenes/S09_Numbers.tsx` | 48-52 | 12 | RELAUNCH | B |
| S10 | `video/src/scenes/S10_Approvals.tsx` | 52-60 | 13-14 | drop | B |
| S11 | `video/src/scenes/S11_Context.tsx` | 60-64 | 15 | drop | B |
| S12 | `video/src/scenes/S12_McpTools.tsx` | 64-72 | 16-17 | drop | B |
| S13 | `video/src/scenes/S13_Walk.tsx` | 72-78 | 18 and half of 19 | drop | B |
| S14 | `video/src/scenes/S14_Recap.tsx` | 78-80 | second half of 19 | drop (run-up) | B |
| S15 | `video/src/scenes/S15_Outro.tsx` | 80-88 (to frame 1347) | 20-21 | FINAL HIT, outro | B |

---

# BLOCK A: beats 0-47 (programmer A, 8 scenes)

## S01 `S01_Hook.tsx`: beats 0-4, bar 0

One phrase per beat, each a hard cut, centered in MIDDLE. Wrap the scene in `ShakeOnBeat`.

| beat | main text (PunchText) | visual and hits |
|---|---|---|
| 0 | `YOUR BINARY` | white, size 240. Flash on the first frame, shake. Transition in: cut from black. |
| 1 | `YOUR MACHINE` | white, size 240. Shake. |
| 2 | `WHY SEND IT` | white, size 240. Shake, split 20. |
| 3 | `ANYWHERE?` | **accent**, size 280, punch-in zoom 1.0 to 1.08 over the beat. Shake (strength x1.5). |

No caption. Transition out: glitch on the last 4 frames, into S02.

## S02 `S02_WhatItIs.tsx`: beats 4-12, bars 1-2

Calmer than the hook. Main text in MIDDLE, one phrase per beat. A caption in BOTTOM stays for the whole scene:

`mafdet agent · experimental`

| beat | main text | visual and hits |
|---|---|---|
| 4 | `A LOCAL MODEL` | white, slides up through a mask (6 frames). Shake on the downbeat. |
| 5 | `ON YOUR MACHINE` | white, hard cut. |
| 6 | `THINKING` | **accent**, size 280, speed-ramp slam. Shake. |
| 7 | `WHILE YOU TYPE` | white. A block cursor blinks after the E (4 frames on, 4 off). |
| 8 | `READS FILES` | white, cut. Shake on the downbeat. |
| 9 | `RUNS COMMANDS` | white, cut. |
| 10 | `USES MCP TOOLS` | white, cut. Shake. |
| 11 | `UNTIL IT'S DONE` | **accent**, punch-in zoom to 1.06. |

Transition out: hard cut to black on beat 12 (no glitch: the next scene must feel empty).

## S03 `S03_Command.tsx`: beats 12-16, bar 3

The bass drops out on beats 13-14. The screen empties with it.

| beat | on screen | visual and hits |
|---|---|---|
| 12 | TOP: `ONE COMMAND` (PunchText, white, size 200). MIDDLE: `CodeReveal`, title `terminal`, one line, typing from beat 12 over 12 frames. | Shake on beat 12. |
| 13 | The top text is gone (cut). Only the terminal, cursor blinking. | No shake, no flash. Silence. |
| 14 | Same. Slow zoom on the terminal, 1.0 to 1.05 over beats 13-14. | Nothing. |
| 15 | TOP: `NOW WATCH` (**accent**, size 240). | Pickup hit: flash, shake (x1.5). On the last 5 frames the whole scene glitches (intensity high) into the drop. |

Code line (exact):
```
> mafdet agent
```

## S04 `S04_Drop.tsx`: beats 16-20, bar 4. THE DROP

The biggest moment of block A. Wrap in `ShakeOnBeat` with every beat as a hit.

| beat | main text | visual and hits |
|---|---|---|
| 16 | `MAFDET` | white, size 340, MIDDLE upper half. **Flash (2 frames), shake x2, glitch 6 frames, split 28.** |
| 17 | `MAFDET` stays, `AGENT` lands under it | `AGENT` in **accent**, size 340. Shake, split 20. |
| 18 | `SEVEN STEPS` | cut, white, size 260. Shake. |
| 19 | `ONE LOOP` | cut, **accent**, size 300, punch-in zoom to 1.1. Shake. |

No caption. Transition out: cut on beat 20, 1-frame white flash.

## S05 `S05_Flow.tsx`: beats 20-32, bars 5-7

One `FlowDiagram`, vertical, filling x 110-970, y 240-1380. Nothing else but one caption at the lower edge of
BOTTOM (y 1420-1490), present from beat 20:

`mafdet/agent/loop.py · at most 40 steps`

Nodes, loop-back and beats are in section 4. Per beat:

| beat | what lands | hits |
|---|---|---|
| 20 | node 0 `TYPE` | shake (downbeat) |
| 21 | node 1 `ENTER` | small shake (x0.5) |
| 22 | node 2 `FIT CONTEXT` | small shake |
| 23 | node 3 `MODEL STEP` | small shake |
| 24 | node 4 `CHECK` | shake (downbeat) |
| 25 | node 5 `RUN TOOL` | small shake |
| 26 | **loop-back edge** RUN TOOL to FIT CONTEXT, drawn in accent | shake x1.5, 1-frame flash, glitch 4 frames |
| 27 | nothing new: the active node runs FIT CONTEXT, MODEL STEP, CHECK, RUN TOOL, a quarter beat each | none |
| 28 | node 6 `ANSWER` | shake (downbeat), flash |
| 29-31 | hold the full diagram so it can be read. Slow punch-in zoom 1.0 to 1.04 over the three beats. The active node goes round the loop once more (FIT CONTEXT, MODEL STEP, CHECK, RUN TOOL, one per half beat, beats 29-30.5), then rests on ANSWER for beat 31. | none on 29-31 |

Note for the programmer: `FlowDiagramProps` has no `active` prop. If the component does not expose the active
node, accept its own behavior (latest node filled) and skip the cycling on beats 27 and 29-31; do not change
`contracts.ts` yourself, ask the orchestrator.

Transition out: hard cut on beat 32.

## S06 `S06_LoopCode.tsx`: beats 32-36, bar 8

Code must be read: the block is on screen for all 4 beats and typed in the first one.

- MIDDLE and BOTTOM (y 620-1380): `CodeReveal`, title `mafdet/agent/loop.py`, font 34, typing from beat 32
  over 14 frames, then still.
- TOP: main text, changing as below.
- Caption under the code, from beat 34: `real sessions · Qwen3-14B · RTX 4070 SUPER`

Code lines (exact, 7 lines; `return reply.content` and `self._call(call)` in accent, `...` in dim):
```
if not reply.tool_calls:
    ...
    return reply.content
for call in reply.tool_calls:
    if self.stop.is_set():
        raise Stopped
    self._call(call)
```

| beat | TOP main text | hits |
|---|---|---|
| 32 | `THE LOOP` (white, size 220) | shake |
| 33 | stays | none |
| 34 | `~60 TOOL CALLS` (white, size 200) | shake |
| 35 | `NONE MALFORMED` (**accent**, size 200) | shake, split 20 |

Transition out: glitch on the last 4 frames.

## S07 `S07_SelfCorrect.tsx`: beats 36-40, bar 9

Hard bar: one phrase per beat on top, with a still terminal under it for all 4 beats.

- BOTTOM (y 1100-1400): `CodeReveal`, title `told back to the model`, font 32, typed from beat 36 over 12
  frames, then still. Lines (exact):
```
The user declined this call. Do not
repeat it: go on another way,
```
- MIDDLE: main text.

| beat | main text | hits |
|---|---|---|
| 36 | `BAD JSON` (white, size 280) | shake, flash |
| 37 | `UNKNOWN TOOL` (white, size 260) | shake |
| 38 | `DECLINED CALL` (white, size 260) | shake |
| 39 | `TOLD BACK` (**accent**, size 300, punch-in zoom to 1.08) | shake x1.5, split 24 |

Transition out: cut to black on beat 40.

## S08 `S08_ThinkAhead.tsx`: beats 40-48, bars 10-11

The scene slows down on purpose and empties into the break (bass cut on beats 45-46, pickup on 47).

Layout: TOP main text. MIDDLE: a `CodeReveal` used as the message box, title `mafdet agent`, font 34, two
lines. Line 1 is the request being typed, from beat 42 to beat 45 (about 3 characters per 2 frames, an
uneven human rhythm is welcome if deterministic). Line 2 is the status line in **accent**, appearing on
beat 43; its number counts up in steps of 32 (`32`, `64`, ... `256`) from beat 43 and lands on `273` on beat 45.

Lines (exact, final state):
```
> read loop.py and explain it
 · thinking ahead 273 tok
```
Caption at the lower edge of BOTTOM, from beat 42 to beat 47:

`needs llama-server and a reasoning model (Qwen3)`

| beat | on screen | hits |
|---|---|---|
| 40 | TOP: `IT THINKS` (white, size 260). Empty message box with a blinking cursor. | shake, 1-frame flash |
| 41 | TOP: `WHILE YOU TYPE` (**accent**, size 220). | shake |
| 42 | Top text stays. Typing starts on line 1. | none |
| 43 | Top text goes (cut). Status line appears: ` · thinking ahead 32 tok`, counting. | small shake |
| 44 | Typing and counting continue. | none |
| 45 | **Break.** The request is complete, the counter lands on `273`. The box frame and the caption stay, everything else is black. | none. Silence. |
| 46 | Hold. Only the cursor blinks. Slow zoom on the status line, 1.0 to 1.08 over beats 45-46. | none |
| 47 | TOP zone empty; `ENTER` slams in MIDDLE over the box (**accent**, size 380). | Pickup: flash, shake x1.5. Last 5 frames: glitch (high intensity) into block B. |

Transition out: the glitch on beat 47 cuts straight into the relaunch.

---

# BLOCK B: beats 48-87 (programmer B, 7 scenes)

## S09 `S09_Numbers.tsx`: beats 48-52, bar 12. THE RELAUNCH

The biggest moment of block B's first half. The number is the hit.

Caption at the lower edge of BOTTOM for the whole scene (two mono lines):

`Qwen3-4B · RTX 2060 · 3 runs`
`a direction, not a result`

| beat | MIDDLE main text | mono caption directly under the main text | hits |
|---|---|---|---|
| 48 | `UNDER 2 S` (**accent**, size 340) | `first tool call after Enter, with notes` | **flash 2 frames, shake x2, glitch 6 frames, split 28** |
| 49 | stays, punch-in zoom 1.0 to 1.1 | same | shake |
| 50 | `WITHOUT NOTES` (white, size 240) | (none) | shake |
| 51 | `UP TO 5.5 S` (`COLORS.dim`, size 300) | `first tool call after Enter, without notes` | shake, split 20 |

Transition out: cut on beat 52.

## S10 `S10_Approvals.tsx`: beats 52-60, bars 13-14

Bar 13: one word per beat. Bar 14: the real line of code held for 4 beats under changing rules.

Bar 13 layout: MIDDLE main text. BOTTOM: a row of three thin 2px boxes (sharp corners) labelled in mono
`ask`, `edits`, `all`; the box of the current mode is filled with the accent.

| beat | main text | bottom row | hits |
|---|---|---|---|
| 52 | `ASK` (white, size 420) | `ask` filled | shake, flash |
| 53 | `EDITS` (white, size 420) | `edits` filled | shake |
| 54 | `ALL` (**accent**, size 420) | `all` filled | shake x1.5 |
| 55 | `SHIFT+TAB` (white, size 260) | the fill runs ask, edits, all, a third of a beat each. Caption under the row: `cycles ask -> edits -> all` | shake |

Bar 14 layout: TOP main text. MIDDLE: `CodeReveal`, title `mafdet/agent/permissions.py`, font 34, typed
from beat 56 over 10 frames, still for the rest. BOTTOM: caption, changing with the beat.

Code line (exact):
```
MODES = ("ask", "edits", "all")
```

| beat | TOP main text | BOTTOM caption | hits |
|---|---|---|---|
| 56 | `WRITES ASK FIRST` (white, size 190) | `ask mode: writes, the shell, MCP tools not read-only` | shake |
| 57 | `SHELL ASKS FIRST` (white, size 190) | same | none |
| 58 | `OUTSIDE ALWAYS ASKS` (white, size 180) | `anything outside the working folder` | shake |
| 59 | `INSTALLS ALWAYS ASK` (**accent**, size 180) | `in every mode` | shake, split 20 |

Transition out: glitch on the last 4 frames.

## S11 `S11_Context.tsx`: beats 60-64, bar 15

Numbers, one per beat, with one half-beat stutter on beat 62.

| beat | MIDDLE main text | caption under it | hits |
|---|---|---|---|
| 60 | `CONTEXT THAT FITS` (white, size 200) | (none) | shake, flash |
| 61 | `4,000 CHARS` (white, size 260) | `each tool output is cut, and kept whole on disk` | shake |
| 62 | `4,496` (`COLORS.dim`, size 380) | `tokens before compaction · 6k context` | shake |
| 62.5 | `2,359` (**accent**, size 380), hard cut, 1-frame flash | `tokens after · the task finished right` | flash, shake |
| 63 | `NOTES STAY` (white, size 300) | `notes and the to-do list are never left out` | shake |

Transition out: cut on beat 64.

## S12 `S12_McpTools.tsx`: beats 64-72, bars 16-17

TOP: main text, one phrase per beat. MIDDLE (y 760-1160): a `CodeReveal` that holds one block for beats
64-68 and another for beats 68-72 (each on screen 4 beats). BOTTOM: caption.

Block 1, title `terminal`, typed from beat 64 over 14 frames (exact; the second line is the same command
wrapped, indented 4 spaces):
```
mafdet agent --mcp rea
    --profile native-static
```
Block 2, title `mafdet/agent/mcp.py`, typed from beat 68 over 12 frames (exact; `{name}` in accent):
```
{name}: on, call it from the next step
```

| beat | TOP main text | BOTTOM caption | hits |
|---|---|---|---|
| 64 | `207 TOOLS` (white, size 300) | `the rea MCP server · 3.4 MB of schemas` | shake, flash |
| 65 | stays, punch-in zoom to 1.06 | same | none |
| 66 | `16 START ON` (**accent**, size 280) | `profile native-static: 16 tools + find_tools` | shake |
| 67 | `1,696 TOKENS` (white, size 260) | same | shake |
| 68 | `FIND_TOOLS` (**accent**, size 280). Code block 2 replaces block 1 (cut). | `the model finds and turns on the rest itself` | shake, glitch 4 frames |
| 69 | `FINDS THE REST` (white, size 240) | same | shake |
| 70 | `TURNS THEM ON` (white, size 240) | same | shake |
| 71 | stays; the code line pulses once (accent frame for 4 frames) | same | none |

Transition out: cut on beat 72.

## S13 `S13_Walk.tsx`: beats 72-78, bar 18 and the first half of bar 19

TOP: main text. MIDDLE (y 700-1100): `CodeReveal`, title `terminal`, font 32 (line 1 is 42 characters: check
it fits the frame, lower the font before wrapping), typed from beat 72 over 16 frames, still until beat 78.
BOTTOM: caption.

Code lines (exact; one command wrapped in two lines):
```
mafdet agent --walk C:\samples\crackme.exe
  --mcp rea --max-functions 50
```

| beat | TOP main text | BOTTOM caption | hits |
|---|---|---|---|
| 72 | `--WALK` (**accent**, size 340) | (none) | shake, flash |
| 73 | `EVERY FUNCTION` (white, size 240) | `of a binary, named from the leaves up` | shake |
| 74 | `LEAVES UP` (white, size 300) | same | shake |
| 75 | `ONE AT A TIME` (white, size 240) | `code drives the walk, the model gets one function` | shake |
| 76 | `86 OF 86` (**accent**, size 360) | `where.exe · functions named · about 25 min` | shake x1.5, split 24 |
| 77 | `0 FAILED` (white, size 320) | `named, not graded: accuracy was not measured` | shake |

Transition out: cut to black on beat 78.

## S14 `S14_Recap.tsx`: beats 78-80, the second half of bar 19

The run-up to the final hit: a half-beat stutter, four cuts, one word each, MIDDLE, size 400, no caption,
no code. Alternate the frame: white text on black, then black text on white (inverted), and so on.

| beat | main text | visual and hits |
|---|---|---|
| 78 | `TYPE` | white on black. Shake. |
| 78.5 | `THINK` | **inverted** (black on white). Shake. |
| 79 | `CHECK` | white on black. Shake. |
| 79.5 | `RUN` | **accent** on black, split 30. Shake. The last 3 frames go fully black (a breath before the hit). |

## S15 `S15_Outro.tsx`: beats 80-88, bars 20-21, to frame 1347

The last full hit is beat 80. After it the track decays: no shake after beat 85.

| beat | on screen | hits |
|---|---|---|
| 80 | MIDDLE upper half: `MAFDET AGENT` on two lines as two PunchTexts (`MAFDET` white, `AGENT` **accent**, size 340 each). | **The biggest hit: flash 2 frames, shake x2, glitch 6 frames, split 30.** |
| 81 | stays, punch-in zoom 1.0 to 1.05. | shake |
| 82 | The name moves up into TOP (slide, 6 frames, scaled to size 200). MIDDLE: `CodeReveal`, title `terminal`, font 44, typing from beat 82 over 12 frames. It stays to the end. | small shake |
| 83 | hold: name and terminal. | none |
| 84 | BOTTOM: `STOP WAITING.` (white, size 180) under the terminal. | shake |
| 85 | `START TYPING.` lands under it (**accent**, size 180). Both lines stay (4 words). | shake, split 20 |
| 86 | hold. Caption at the lower edge of BOTTOM: `experimental · a local model, on your machine` | none |
| 87 to end | hold, then everything but the terminal line fades to black over the last 12 frames; the cursor blinks to the last frame. | none |

Code line (exact):
```
> mafdet agent
```
Layout check for beats 84-87: name in TOP (y 220-520), terminal in MIDDLE (y 640-860), the two motto lines
at y 960-1330, the caption at y 1430-1490. The main text on screen is `STOP WAITING. START TYPING.` (4
words); the name is a title, kept smaller than the motto.

---

## 4. FlowDiagram (S05)

`nodes` in order, with `appear` given as beats (convert with `f(b) - f(20)`):

| index | label | note (mono, at most 34 characters) | appears on beat |
|---|---|---|---|
| 0 | `TYPE` | `model notes in 32-token chunks` | 20 |
| 1 | `ENTER` | `first step starts from the notes` | 21 |
| 2 | `FIT CONTEXT` | `old outputs left out or compacted` | 22 |
| 3 | `MODEL STEP` | `reasoning + text + tool calls` | 23 |
| 4 | `CHECK` | `args · tool exists · approval` | 24 |
| 5 | `RUN TOOL` | `output clipped and saved` | 25 |
| 6 | `ANSWER` | `no tool calls: the turn ends` | 28 |

Loop-back edge: `loopBack = {from: 5, to: 2, at: f(26) - f(20)}`, RUN TOOL back up to FIT CONTEXT, on beat 26.

Active node (accent fill), if the component exposes it: the latest node on beats 20-26; FIT CONTEXT, MODEL
STEP, CHECK, RUN TOOL for a quarter beat each on beat 27; ANSWER on beat 28; one more round of the loop on
beats 29-30.5 (half a beat per node); ANSWER on beat 31.

## 5. Implementation blocks

| | BLOCK A | BLOCK B |
|---|---|---|
| beats | 0-47 (frames 0-734) | 48-87 (frames 735-1346) |
| scenes | S01 to S08 (8) | S09 to S15 (7) |
| big moment | the drop, beat 16 (S04) | the relaunch, beat 48 (S09), and the final hit, beat 80 (S15) |
| quiet moment | bass out, beats 13-14 (S03); the break, beats 45-46 (S08) | the outro decay, beats 86-87 (S15) |
| FlowDiagram | S05 | none |
| CodeReveal | S03, S06, S07, S08 | S10, S12 (two blocks), S13, S15 |
| half-beat stutter | none (quarter-beat node cycling in S05 only) | S11 beat 62, S14 beats 78-79 |
| custom drawing | none | the three mode boxes in S10 |

Hand-over between the blocks: S08 ends on a 5-frame glitch on the pickup (beat 47) and S09 opens on its own
flash and glitch on beat 48. Neither needs the other's code. Both use only the 7 components of
`contracts.ts` plus plain `div`s for captions and the S10 boxes.

## 6. Fact check

Line numbers are those of `docs/mafdet_brief.md`.

| scene | on screen | supported by |
|---|---|---|
| S01 | `YOUR BINARY / YOUR MACHINE / WHY SEND IT / ANYWHERE?` | line 6: built first for reverse engineering, "where the binary should not leave the machine". A question, no claim. |
| S02 | `A LOCAL MODEL / ON YOUR MACHINE` | line 6: "puts a local model to work on your machine in a loop" |
| S02 | `THINKING / WHILE YOU TYPE` | line 10: "while you type, the model reasons inside the real conversation" |
| S02 | `READS FILES / RUNS COMMANDS / USES MCP TOOLS / UNTIL IT'S DONE` | line 6: "reads and writes files, runs commands and uses the tools of MCP servers ... until the task is done" |
| S02, S15 | caption `experimental` | line 3: "The README labels the feature experimental" |
| S03, S15 | `> mafdet agent`, `ONE COMMAND` | line 6: "`mafdet agent` puts a local model to work" |
| S04 | `SEVEN STEPS / ONE LOOP` | lines 17-24: the 7-step flow; line 9: tool loop |
| S05 | node TYPE, `model notes in 32-token chunks` | line 18 |
| S05 | node ENTER, `first step starts from the notes` | lines 19 and 10 |
| S05 | node FIT CONTEXT, `old outputs left out or compacted` | line 20 |
| S05 | node MODEL STEP, `reasoning + text + tool calls` | line 21 |
| S05 | node CHECK, `args · tool exists · approval` | line 22 |
| S05 | node RUN TOOL, `output clipped and saved`, loop back to FIT CONTEXT | line 23 |
| S05 | node ANSWER, `no tool calls: the turn ends` | line 24 |
| S05 | caption `mafdet/agent/loop.py · at most 40 steps` | line 9: "(`loop.py`, max 40 steps)" |
| S06 | the 7 code lines | lines 27-36, snippet A |
| S06 | `~60 TOOL CALLS / NONE MALFORMED`, caption `real sessions · Qwen3-14B · RTX 4070 SUPER` | line 72: "about 60 tool calls, none malformed" |
| S07 | `BAD JSON / UNKNOWN TOOL / DECLINED CALL / TOLD BACK` | line 9: "Bad JSON, unknown tools and declined calls are told back to the model" |
| S07 | the 2 code lines | lines 58-62, snippet E (`loop.py:268`) |
| S08 | `IT THINKS / WHILE YOU TYPE` | line 10 |
| S08 | ` · thinking ahead 273 tok` | lines 43-48, snippet C without the face; 273 is measured (line 48, line 66) |
| S08 | the counter in steps of 32 | line 18: "notes in 32-token chunks" |
| S08 | caption `needs llama-server and a reasoning model (Qwen3)` | line 10; line 83 |
| S08 | `ENTER` | line 19 |
| S09 | `UNDER 2 S`, `first tool call after Enter, with notes` | line 67: 0.85-1.75 s; line 70: say "under 2 s vs up to 5.5 s" |
| S09 | `WITHOUT NOTES / UP TO 5.5 S` | line 68: 3.5-5.5 s |
| S09 | `Qwen3-4B · RTX 2060 · 3 runs` | line 66 |
| S09 | `a direction, not a result` | line 70 |
| S10 | `ASK / EDITS / ALL`, the code line | lines 37-40, snippet B (`permissions.py:20`) |
| S10 | `SHIFT+TAB`, `cycles ask -> edits -> all` | line 41 |
| S10 | `WRITES ASK FIRST / SHELL ASKS FIRST`, `ask mode: writes, the shell, MCP tools not read-only` | line 11: "Writes, the shell and MCP tools not marked read-only ask first" |
| S10 | `OUTSIDE ALWAYS ASKS`, `anything outside the working folder` | line 11: "Anything outside the folder always asks" |
| S10 | `INSTALLS ALWAYS ASK`, `in every mode` | line 11: "installs ask in every mode" |
| S11 | `4,000 CHARS`, `each tool output is cut, and kept whole on disk` | line 12 |
| S11 | `4,496` then `2,359`, `6k context`, `the task finished right` | line 73 |
| S11 | `NOTES STAY`, `notes and the to-do list are never left out` | line 12 |
| S12 | `207 TOOLS`, `the rea MCP server · 3.4 MB of schemas` | line 71 |
| S12 | `16 START ON`, `1,696 TOKENS`, `profile native-static: 16 tools + find_tools` | lines 13 and 71 |
| S12 | `FIND_TOOLS / FINDS THE REST / TURNS THEM ON` | line 13: "the model finds and turns on the rest itself" |
| S12 | `mafdet agent --mcp rea --profile native-static` (wrapped) | lines 50-52, snippet D |
| S12 | `{name}: on, call it from the next step` | lines 58-60, snippet E (`mcp.py:321`) |
| S13 | the walk command in two lines | lines 53-56, snippet D |
| S13 | `EVERY FUNCTION / LEAVES UP / ONE AT A TIME` and both captions | line 14 |
| S13 | `86 OF 86 / 0 FAILED`, `where.exe · functions named · about 25 min` | line 74 |
| S13 | `named, not graded: accuracy was not measured` | line 82 (the reverse of a forbidden claim) |
| S14 | `TYPE / THINK / CHECK / RUN` | lines 18, 10, 22, 23 |
| S15 | `MAFDET AGENT` | title, line 1 |
| S15 | `STOP WAITING. START TYPING.` | a motto, no claim; it echoes line 10 |
| S15 | `experimental · a local model, on your machine` | lines 3 and 6 |

Nothing on screen mentions cloud providers, `--refine`, the web or background jobs, `need_tool` installs, a
sandbox, 6 GB cards, or a comparison with other agents (section 6 of the brief).
