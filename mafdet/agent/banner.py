"""The top of `mafdet agent`: a cheetah sitting next to a small ball of yarn, live, beside the MAFDET
wordmark, the version and the model, and under it a line about cats and code, a new one at each start.

Drawn in pixels with half blocks (two pixels a character, art.halfblocks), in a sober palette: cream
and muted gold, dark spots and tear marks, amber eyes. The cheetah sits still, as cats do; now and then
it blinks, its tail flicks, and it lifts a paw to tap the ball, which rolls off and comes slowly back.
The wordmark is the identity's own (identity.WORDMARK), rasterized at the terminal's resolution. One frame is a pure function of the frame number (and of whether it falls on a
blink), so the scene is the same in any terminal and can be tested.
"""
from __future__ import annotations

import random

from rich.text import Text

from .. import __version__
from ..identity import PAPER, WORDMARK, WORDMARK_SIZE
from ..tui.art import _inside, _polygons, halfblocks
from ..tui.fx import DIM, HOT, TEXT

PALETTE = {"F": "#D8B276",      # fur
           "L": "#EFDDBA",      # cream: chest, inside of the legs
           "S": "#A07A48",      # fur in shade
           "K": "#33251A",      # spots, tear marks
           "E": "#E0952E",      # eyes
           "N": "#7A5040",      # nose
           "W": "#F6EEDF",      # muzzle, the tip of the tail, the paw
           "Y": "#B8732C",      # the ball
           "H": "#D99A50",      # its light side
           "Z": "#8A5222"}      # its shade

# The cheetah sitting, facing us, the haunch behind and the tail along the floor.
CHEETAH = ["........F...F...",
           "........FF.FF...",
           ".......FFFFFFF..",
           "......FFFFFFFFF.",
           "......FFEFFFEFF.",
           "......FFKFNFKFF.",
           ".......FFKWKFF..",
           "........FLWLF...",
           ".......SFLLLF...",
           "......SFFLLLLF..",
           ".....SFFKFLLLF..",
           "....SFFFFFFLLF..",
           "...SFKFFSFFSLF..",
           "...SFFFFFKFSLF..",
           "..SFFSFFFFFSLFF.",
           "..SFKFFFKFFSLLF.",
           "..SFFFFSFFFSLLF.",
           "..SFFFFFFFSLLLF.",
           ".SFKFFSFFKFFFFFF",
           "..SSFFFFSFFFKFFS"]
EYES = ((4, 8), (4, 12))
# The tip of the tail, down or flicked up: (row, column, colour).
TIP = [[(17, 17, "F"), (17, 18, "W"), (18, 16, "F"), (18, 17, "F"), (18, 18, "F")],
       [(16, 18, "W"), (17, 17, "F"), (17, 18, "F"), (18, 16, "F"), (18, 17, "F")]]
# The paw lifted to tap the ball, and reaching it.
PAW = {"up": [(13, 14, "L"), (13, 15, "L"), (14, 14, "L"), (14, 15, "W")],
       "reach": [(14, 14, "F"), (15, 15, "L"), (15, 16, "L"), (15, 17, "W"), (16, 15, "L"), (16, 16, "W")]}
BALL = [".YY.",
        "YHYZ",
        "YYZZ",
        ".ZZ."]
SPIN = ["YHYZ", "HYYZ", "YYHZ", "YYZH"]          # the light moving round as it rolls

H = 20                      # pixels: 10 lines of text
REST = 20                   # the ball's left edge at rest, just past the tail
W = REST + 7 + len(BALL[0])
FPS = 8
LOOP = 64
# The ball's offset from REST over a loop: still, tapped (frames 30-33), off and slowly back.
_OFF = [0] * 32 + [1, 3, 5, 6, 7, 7, 7, 7] + [7 - round(7 * i / 24) for i in range(1, 25)]


def _paw(f: int) -> str | None:
    return "up" if 26 <= f < 30 else "reach" if 30 <= f < 33 else None


def frame(f: int, blink: bool = False) -> list[list[str | None]]:
    """The scene at frame `f` as a grid of colours (None = empty)."""
    grid: list[list[str | None]] = [[None] * W for _ in range(H)]

    def put(r: int, c: int, ch: str):
        if 0 <= r < H and 0 <= c < W:
            grid[r][c] = PALETTE[ch]

    f %= LOOP
    for r, row in enumerate(CHEETAH):
        for c, ch in enumerate(row):
            if ch != ".":
                put(r, c, ch)
    for r, c, ch in TIP[1 if (f // 6) % 5 == 2 else 0]:
        put(r, c, ch)
    paw = _paw(f)
    if paw:
        for r, c, ch in PAW[paw]:
            put(r, c, ch)
    for r, c in EYES:
        put(r, c, "S" if blink else "E")

    x = REST + _OFF[f]
    for r, row in enumerate(BALL):
        if r == 1:
            row = SPIN[x % len(SPIN)]
        for c, ch in enumerate(row):
            if ch != ".":
                put(H - len(BALL) + r, x + c, ch)
    return grid


def _wordmark(height: int) -> list[list[str | None]]:
    """identity.WORDMARK at `height` pixels, in paper. Each letter starts on a whole pixel, so that its
    stems come out one pixel wide like the others' instead of smeared over two."""
    scale = WORDMARK_SIZE[1] / height
    width = round(WORDMARK_SIZE[0] / scale)
    grid: list[list[str | None]] = [[None] * width for _ in range(height)]
    for offset, d in WORDMARK:
        polys, left = _polygons(d), round(offset / scale)
        for r in range(height):
            for c in range(min(round(110 / scale), width - left)):
                hits = sum(_inside((c + (j + .5) / 4) * scale, (r + (i + .5) / 4) * scale, polys)
                           for i in range(4) for j in range(4))
                if hits > 6:
                    grid[r][left + c] = PAPER
    return grid


NAME = _wordmark(8)
SIDE = len(NAME[0]) + 4                   # columns the wordmark takes next to the scene, with the gap


# A line under the scene, a new one at each start: cats, cheetahs and code.
QUIPS = {
    "it": ["Il ghepardo fa da 0 a 100 in tre secondi. Il tuo npm install, in tre caffè.",
           "Ho guardato sotto il divano: nessun bug. Solo un gomitolo e tre tappi di penna.",
           "Un gatto ha sette vite. Il tuo processo, dopo un kill -9, nemmeno una.",
           "git push --force: buttare il bicchiere giù dal tavolo guardandoti negli occhi.",
           "Ogni tanto cammino sulla tastiera. Si chiama fuzzing, ed è una cosa seria.",
           "Ho trovato un bug. L'ho lasciato sullo zerbino. Non c'è di che.",
           "Non è un loop infinito: mi sto inseguendo la coda.",
           "Dormo diciotto ore al giorno. Le altre sei compilo.",
           "La mia complessità è O(1): un pisolino, qualunque sia l'input.",
           "Non rompo la build. La spingo piano piano giù dal bordo del tavolo.",
           "Ho preso il puntatore laser. Purtroppo anche il puntatore nullo.",
           "Miao è il mio Hello World.",
           "Faccio code review da gatto: fisso a lungo, giudico in silenzio, me ne vado.",
           "Stack overflow: troppi gatti impilati sullo stesso cuscino.",
           "Ho rovesciato un vaso, due tazze e tre test. Il vaso non era coperto da test.",
           "Per capire la ricorsione devi prima inseguire la tua coda.",
           "Ti porto i bug come le lucertole: vivi, e alle sei del mattino.",
           "Cache miss: ho mancato il salto sul frigorifero. Riprovo.",
           "Non è un deadlock, è un pisolino condiviso.",
           "Merge conflict: due gatti, una scatola sola.",
           "Penso mentre scrivi: come aspettare davanti alla ciotola prima che tu la riempia.",
           "Mi sono seduto sul portatile e si è chiuso. Ora i test passano tutti.",
           "Tab o spazi? Io preferisco sdraiarmi sulla tastiera.",
           "Ogni scatola vuota è un container. In pratica sono Docker.",
           "Se ci entro, mi ci siedo. Se non ci entro, è un off-by-one.",
           "Io ho le macchie per natura. Il tuo codice, per scelta.",
           "Il tuo codice è pronto quando smetto di fissarlo. Cioè mai.",
           "Leggo mentre scrivi. I typo li vedo, ma faccio finta di niente. Quasi.",
           "Undefined behavior: sono un gatto, è il mio comportamento predefinito.",
           "Sprint brevi e scatti improvvisi: sono agile da prima che fosse di moda."],
    "en": ["A cheetah goes 0 to 100 in three seconds. Your npm install, in three coffees.",
           "Checked under the sofa: no bugs. Just a ball of yarn and three pen caps.",
           "Cats have nine lives. Your process, after kill -9, has none.",
           "git push --force: knocking the glass off the table while holding eye contact.",
           "Sometimes I walk on the keyboard. It's called fuzzing, and it's a real technique.",
           "Found a bug. Left it on your doormat. You're welcome.",
           "It's not an infinite loop. I'm chasing my tail.",
           "I sleep eighteen hours a day. The other six, I compile.",
           "My complexity is O(1): one nap, whatever the input.",
           "I don't break the build. I nudge it gently off the edge of the table.",
           "Caught the laser pointer. Also the null pointer, unfortunately.",
           "Meow is my Hello World.",
           "I review code like a cat: stare for a long time, judge in silence, walk away.",
           "Stack overflow: too many cats on one cushion.",
           "Knocked over a vase, two mugs and three tests. The vase had no coverage.",
           "To understand recursion, you must first chase your own tail.",
           "I bring you bugs the way I bring you lizards: alive, at 6 a.m.",
           "Cache miss: I missed the jump onto the fridge. Retrying.",
           "It's not a deadlock, it's a shared nap.",
           "Merge conflict: two cats, one box.",
           "I think while you type: like waiting by the bowl before you fill it.",
           "Sat on the laptop and it closed. All tests pass now.",
           "Tabs or spaces? I prefer lying on the keyboard.",
           "Every empty box is a container. I'm basically Docker.",
           "If it fits, I sits. If it doesn't, it's an off-by-one.",
           "My spots are natural. Your code's are a choice.",
           "Your code is ready when I stop staring at it. So, never.",
           "I read while you type. I see the typos and say nothing. Mostly.",
           "Undefined behavior? I'm a cat. That's my default behavior.",
           "Short sprints and sudden bursts: I was agile before it was cool."],
}


def quip(language: str | None = None, rng: random.Random | None = None) -> str:
    """One of the lines, in the app's language (English when there are none in it)."""
    if language is None:
        try:
            from ..core.config import load
            language = load().get("language", "en")
        except Exception:
            language = "en"
    return (rng or random).choice(QUIPS.get(language) or QUIPS["en"])


def banner(f: int, label: str, blink: bool = False, width: int = 120, line: str = "") -> Text:
    """The cheetah at frame `f` with the wordmark, the version and the model on its right (only the words,
    on a line under it, when the terminal is narrow), and `line` below."""
    art = halfblocks(frame(f, blink)).split("\n")
    beside = width >= W + SIDE
    side: dict[int, Text] = {}
    if beside:
        for i, row in enumerate(halfblocks(NAME).split("\n")):
            side[2 + i] = row
        room = width - W - 4
        side[7] = Text.assemble(("agent", HOT), ("  ·  v" + __version__, DIM))
        side[8] = Text(label if len(label) <= room else label[:max(0, room - 1)] + "…", style=TEXT)
    out = Text(no_wrap=True, overflow="crop")
    for i in range(H // 2):
        row = art[i] if i < len(art) else Text()
        out.append_text(row)
        if i in side:
            out.append(" " * (W - row.cell_len + 4))
            out.append_text(side[i])
        out.append("\n")
    if not beside:
        out.append_text(Text.assemble(("MAFDET", f"bold {PAPER}"), ("  agent  v" + __version__ + "  ", DIM),
                                      (label, TEXT)))
        out.append("\n")
    if line:
        out.append("\n")
        out.append(line, style=f"italic {DIM}")
    out.rstrip()
    return out


def height(width: int, line: bool = True) -> int:
    """The lines `banner` takes at this width."""
    return H // 2 + (0 if width >= W + SIDE else 1) + (2 if line else 0)


__all__ = ["banner", "frame", "height", "quip", "FPS"]
