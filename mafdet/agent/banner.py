"""The top of `mafdet agent`: a little pixel-art cheetah playing with a ball of yarn, live, next to
the name in a pixel font, the version and the model, and under it a line about cats and code, a new
one at each start.

Everything is drawn in pixels with half blocks (two pixels a character, art.halfblocks), the name
too, so the two belong together: its letters go from the cheetah's gold to the yarn's coral. The
cheetah sits; the ball rolls in, the cheetah bats it away with a paw (shutting its eyes, pleased),
the ball rolls off trailing its thread and comes back. Its ringed tail swishes, now and then it
blinks. One frame is a pure function of the frame number (and of whether it falls on a blink), so
the scene is the same in any terminal and can be tested.
"""
from __future__ import annotations

import random

from rich.text import Text

from .. import __version__
from ..tui.art import halfblocks
from ..tui.fx import DIM, HOT, TEXT

PALETTE = {"y": "#F2B65A",      # golden fur
           "l": "#FFEBC8",      # cream: muzzle, chest, paws
           "k": "#2B1B0F",      # spots, tear marks, eyes, mouth
           "w": "#FFFFFF",      # the light in the eyes, the tip of the tail
           "p": "#F59C9A",      # ears, cheeks, nose
           "o": "#9A6428"}      # the rings of the tail (black would vanish on the black screen)
YARN, SHADE, LIGHT, STRAND = "#EF6F5E", "#C04A3C", "#FFA894", "#93342A"

# The cheetah, sitting, big head and small body. The eyes (2x2) and the right front paw move.
CHEETAH = ["..yy........yy..",
           ".yppy......yppy.",
           ".yyyyyyyyyyyyyy.",
           "ykyyyyykkyyyyyky",
           "yy..yyyyyyyy..yy",
           "yy..yyyyyyyy..yy",
           "yppkyllllllykppy",
           "yyyykllppllkyyyy",
           ".yyyllkllkllyyy.",
           "..yyyllkkllyyy..",
           "...yyyyyyyyyy...",
           "...ykyllllyky...",
           "...yyyllllyyy...",
           "...lllyyyy......"]
EYES = ((4, 2), (4, 12))
PAW = {"rest": [(13, 10, "l"), (13, 11, "l"), (13, 12, "l")],
       "bat":  [(11, 13, "y"), (11, 14, "y"), (11, 15, "l"), (12, 13, "y"), (12, 14, "l"), (12, 15, "l")]}
X = 4                       # where the cheetah starts: its tail curls up on the left
# The tail, one pixel wide from the back of the body up beside the cheek, in three positions.
TAIL = [[(13, 2), (13, 1), (12, 0), (11, -1), (10, -1), (9, -1), (8, 0)],
        [(13, 2), (13, 1), (12, 0), (11, -1), (10, -2), (9, -2), (8, -2)],
        [(13, 2), (13, 1), (13, 0), (12, -1), (11, -2), (10, -3), (9, -3)]]

# The ball: x yarn, s shade, h light; its strands in three turns of rolling.
BALL = [".xxxx.",
        "xhhxxx",
        "xhxxxs",
        "xxxxxs",
        "xxxxss",
        ".ssss."]
STRANDS = [[(1, 3), (2, 2), (3, 1), (2, 4), (3, 3)],
           [(1, 2), (2, 2), (3, 2), (2, 4), (4, 3)],
           [(1, 1), (2, 2), (3, 3), (1, 4), (4, 2)]]

H = 14                      # pixels: 7 lines of text
NEAR = X + 16               # the ball's left edge where the paw reaches it
ROLL = [0, 3, 6, 8, 9, 10, 10, 10, 10, 10, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0]   # how far it is, frame by frame
LOOP = len(ROLL)
W = NEAR + max(ROLL) + len(BALL[0])
FPS = 8

# The name in a pixel font of its own, 7 pixels tall, lower case.
FONT = {"m": [".....", ".....", "####.", "#.#.#", "#.#.#", "#.#.#", "#.#.#"],
        "a": ["....", "....", ".###", "...#", ".###", "#..#", ".###"],
        "f": ["..##", ".#..", "####", ".#..", ".#..", ".#..", ".#.."],
        "d": ["...#", "...#", ".###", "#..#", "#..#", "#..#", ".###"],
        "e": ["....", "....", ".##.", "#..#", "####", "#...", ".###"],
        "t": [".#.", ".#.", "###", ".#.", ".#.", ".#.", ".##"]}
NAME_FROM, NAME_TO = (0xF2, 0xB6, 0x5A), (0xEF, 0x6F, 0x5E)     # the fur's gold to the yarn's coral


def frame(f: int, blink: bool = False) -> list[list[str | None]]:
    """The scene at frame `f` as a grid of colours (None = empty)."""
    grid: list[list[str | None]] = [[None] * W for _ in range(H)]

    def put(r: int, c: int, colour: str | None):
        if 0 <= r < H and 0 <= c < W:
            grid[r][c] = colour

    f %= LOOP
    tail = TAIL[(0, 1, 2, 1)[(f // 3) % 4]]
    for i, (r, c) in enumerate(tail):
        put(r, X + c, PALETTE["w"] if i == len(tail) - 1 else PALETTE["o"] if i % 2 else PALETTE["y"])
    for r, row in enumerate(CHEETAH):
        for c, ch in enumerate(row):
            if ch != ".":
                put(r, X + c, PALETTE[ch])
    batting = f in (0, 1)
    for r, c, ch in PAW["bat" if batting else "rest"]:
        put(r, X + c, PALETTE[ch])
    for r, c in EYES:                   # open with a light in them; shut, pleased, while batting or blinking
        shut = blink or batting
        put(r, X + c, PALETTE["y" if shut else "w"])
        put(r, X + c + 1, PALETTE["y" if shut else "k"])
        put(r + 1, X + c, PALETTE["k"])
        put(r + 1, X + c + 1, PALETTE["k"])

    x = NEAR + ROLL[f]
    for c in range(X + 13, x):          # the thread, from under the paws to the ball
        put(H - 1 if (c // 3) % 2 else H - 2, c, YARN)
    top = H - len(BALL)
    for r, row in enumerate(BALL):
        for c, ch in enumerate(row):
            if ch != ".":
                put(top + r, x + c, {"x": YARN, "s": SHADE, "h": LIGHT}[ch])
    away = f < LOOP // 2
    for r, c in STRANDS[(f // 2 if away else -f // 2) % len(STRANDS)]:
        put(top + r, x + c, STRAND)
    return grid


def name() -> list[list[str | None]]:
    """'mafdet' in the pixel font, 8 pixels tall (4 lines), gold to coral from left to right."""
    rows: list[str] = [""] * 8
    for ch in "mafdet":
        glyph = FONT[ch]
        width = max(len(g) for g in glyph)
        for i in range(7):
            rows[i] += glyph[i].ljust(width, ".") + "."
    rows[7] = "." * len(rows[0])
    n = len(rows[0]) - 1
    grid = []
    for row in rows:
        line: list[str | None] = []
        for c, ch in enumerate(row):
            t = c / max(1, n - 1)
            line.append("#%02X%02X%02X" % tuple(round(a + (b - a) * t) for a, b in zip(NAME_FROM, NAME_TO))
                        if ch == "#" else None)
        grid.append(line)
    return grid


NAME = name()
SIDE = 4 + max(len(NAME[0]), 18)       # columns the name and the lines under it take next to the scene


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
    """The cheetah at frame `f` with the name, the version and the model on its right (on a line under
    it when the terminal is narrow), and `line` below."""
    art = halfblocks(frame(f, blink)).split("\n")
    beside = width >= W + SIDE
    room = width - W - 4
    side: dict[int, Text] = {}
    if beside:
        for i, row in enumerate(halfblocks(NAME).split("\n")):
            side[1 + i] = row
        side[5] = Text.assemble(("agent", HOT), ("  v" + __version__, DIM))
        side[6] = Text(label if len(label) <= room else label[:max(0, room - 1)] + "…", style=TEXT)
    out = Text(no_wrap=True, overflow="crop")
    for i in range(H // 2):
        row = art[i] if i < len(art) else Text()
        out.append_text(row)
        if i in side:
            out.append(" " * (W - row.cell_len + 4))
            out.append_text(side[i])
        out.append("\n")
    if not beside:
        out.append_text(Text.assemble(("mafdet", f"bold {HOT}"), (" agent  v" + __version__ + "  ", DIM),
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
