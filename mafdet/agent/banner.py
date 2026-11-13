"""The top of `mafdet agent`: a pixel-art cheetah playing with a ball of yarn, live, next to the
wordmark, the version and the model, and under it a line about cats and code, a new one at each start.

The scene is a grid of pixels drawn with half blocks (two pixels a character, art.halfblocks). The
cheetah sits on the left; the ball rolls toward it, the cheetah lifts a paw and bats it away, the
ball rolls off to the right trailing its thread and comes back. Its eyes follow the ball, its ringed
tail swishes, now and then it blinks. One frame is a pure function of the frame number (and of
whether it falls on a blink), so the scene is the same in any terminal and can be tested.
"""
from __future__ import annotations

import random

from rich.text import Text

from .. import __version__
from ..identity import AMBER
from ..tui.art import WORDMARK, halfblocks
from ..tui.fx import DIM, HOT, TEXT

# The cheetah's coat, and the yarn.
PALETTE = {"y": "#E2A548",      # golden fur
           "d": "#B27530",      # fur in shade
           "l": "#F4DBAE",      # cream: chest, belly, inside of the legs
           "w": "#FFF5E4",      # white: muzzle, toes, the tip of the tail
           "k": "#1E140B",      # spots, tear marks, rings
           "o": "#6A4318",      # the darker edge that keeps the shapes apart
           "n": "#3A2216",      # nose
           "e": "#F9C94E",      # iris
           "p": "#E89A86"}      # inside of the ears
YARN, SHADE, LIGHT, STRAND = "#E8604A", "#B23F2C", "#FF9C80", "#7E2618"

H = 22                      # pixels: 11 lines of text
CAT_X = 9                   # where the cheetah's sprite starts (the tail lies to its left)
FPS = 8

# The cheetah crouched to pounce, facing right, the head turned to us: the body, then the head on it.
# Letters are PALETTE's colours, '.' is empty. The near front leg, the eyes and the tail are drawn apart.
BODY = (11, 0, ["........ooooooo.................",
                ".....oooyykyyyyoo...............",
                "...ooyyyyyyyyykyyolllllllo......",
                "..oyykyyyykyyyyyyyollllllo......",
                ".oyyyyyyyyyyyykyyydllllldo......",
                ".oyykyyyyykyyyyyyyydllldo.......",
                "oyyyyyykyyyyyyykyyyydlldo.......",
                "oyykyyddddyyykyyyyyyydldo.......",
                "oyyyydyykyddyyyyyyyyyydko.......",
                "odyyyydyyyydyyyyyyyyyyddo.......",
                "oowwwwwoddddoowwwwwwooooo......."])
HEAD = (0, 10, ["..ooo..........ooo..",
                ".oppyo........oyppo.",
                ".oyyyyooooooooyyyyo.",
                "oyyyyykyykkyykyyyyyo",
                "oyyyyyyyyyyyyyyyyyyo",
                "oyyy...kyyyyk...yyyo",
                "oyyy...kyyyyk...yyyo",
                "oyyyyyykllllkyyyyyyo",
                ".oyyyyykwnnwkyyyyyo.",
                ".oyyyykwwwwwwkyyyyo.",
                "..oyyykwkwwkwkyyyo..",
                "...oyyykwwwwkyyyo..."])
EYES = ((5, 14), (5, 23))              # the top left pixel of each eye in the sprite, 3 wide and 2 tall

# The near front leg: on the floor, raised, batting forward. (row, column) of its top left in the sprite.
LEG = {"rest": (13, 23, ["oo.....",
                         "lyo....",
                         "lyyo...",
                         "lyyyo..",
                         "yykyo..",
                         "yyyyo..",
                         "yykyyo.",
                         "lyyyyo.",
                         "wwwwwo."]),
       "up":   (12, 23, ["ooooooo.....",
                         "lyyyyyyoooo.",
                         "lyykyyyyywwo",
                         "lyyyykyyywwo",
                         "oyyyoooooo..",
                         ".ooo........"]),
       "bat":  (14, 23, ["lyyo.........",
                         "lyyyyyyoooo..",
                         "lyykyyyyykwwo",
                         "oyyyykyyyywwo",
                         ".oooooooooo.."])}

# The tail: a path of pixels from the rump along the floor and up, drawn two pixels thick, with black
# rings (dark brown: black would vanish on the black screen) and a white tip; three positions of a swish.
TAIL = [[(19, 0), (19, -1), (19, -2), (19, -3), (18, -4), (17, -5), (16, -5), (15, -5), (14, -5), (13, -4),
         (12, -3), (12, -2)],
        [(19, 0), (19, -1), (19, -2), (19, -3), (19, -4), (18, -5), (17, -6), (16, -6), (15, -6), (14, -6),
         (13, -6), (12, -5)],
        [(19, 0), (19, -1), (19, -2), (19, -3), (19, -4), (19, -5), (18, -6), (17, -7), (16, -7), (15, -8),
         (14, -8), (13, -8)]]

# The ball, 10 pixels across: x yarn, s shade, h light; and its strands in four turns of rolling.
BALL = ["...xxxx...",
        ".xhhxxxxx.",
        ".hhxxxxxxs",
        "xhxxxxxxxs",
        "xxxxxxxxxs",
        "xxxxxxxxss",
        "xxxxxxxxss",
        ".xxxxxxss.",
        ".sxxxssss.",
        "...ssss..."]
STRANDS = [[(2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (2, 6), (3, 6), (6, 2), (7, 3), (4, 2)],
           [(2, 5), (3, 5), (4, 5), (5, 5), (6, 5), (3, 2), (4, 3), (5, 7), (6, 7), (7, 4)],
           [(2, 7), (3, 6), (4, 5), (5, 4), (6, 3), (2, 3), (3, 3), (6, 7), (7, 6), (4, 7)],
           [(4, 2), (4, 3), (4, 4), (4, 5), (4, 6), (4, 7), (2, 4), (3, 5), (6, 4), (7, 5)]]

NEAR = CAT_X + 36           # the ball's left edge where the paw reaches it
FAR = NEAR + 12             # and where it stops after the bat
W = FAR + len(BALL[0]) + 1  # the scene's width in pixels
LOOP = 48


def _ball_x(f: int) -> tuple[int, int]:
    """(left edge, hop) of the ball at frame `f` of the loop."""
    f %= LOOP
    if f < 8:                                   # batted: fast, slowing down, a little hop
        t = f / 8
        return NEAR + round((FAR - NEAR) * (1 - (1 - t) ** 2)), (2 if f in (1, 2) else 1 if f == 3 else 0)
    if f < 18:                                  # resting far away
        return FAR, 0
    t = (f - 18) / (LOOP - 18)                  # rolling back to the cheetah
    return FAR - round((FAR - NEAR) * t), 0


def _leg(f: int) -> str:
    f %= LOOP
    if f >= LOOP - 4:
        return "up"
    if f < 3:
        return "bat"
    return "rest"


def frame(f: int, blink: bool = False) -> list[list[str | None]]:
    """The scene at frame `f` as a grid of colours (None = empty)."""
    grid: list[list[str | None]] = [[None] * W for _ in range(H)]

    def put(r: int, c: int, colour: str | None):
        if colour and 0 <= r < H and 0 <= c < W:
            grid[r][c] = colour

    def sprite(top: int, left: int, rows: list[str]):
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch != ".":
                    put(top + r, left + c, PALETTE[ch])

    # The tail first, so the body covers its root.
    tail = TAIL[(0, 1, 2, 1)[(f // 4) % 4]]
    for i, (r, c) in enumerate(tail):
        colour = PALETTE["w"] if i >= len(tail) - 2 else PALETTE["o"] if i % 3 == 2 else PALETTE["y"]
        nr, nc = tail[min(i + 1, len(tail) - 1)]
        across = (1, 0) if abs(nc - c) >= abs(nr - r) else (0, 1)   # thickness across the way it goes
        put(r, CAT_X + c, colour)
        put(r + across[0], CAT_X + c + across[1], colour)
    for top, left, rows in (BODY, HEAD, LEG[_leg(f)]):
        sprite(top, CAT_X + left, rows)

    x, hop = _ball_x(f)
    # The eyes: amber with the pupil on the ball's side, lid and lashes for a blink.
    pupil = 2 if x > NEAR + 3 else 1
    for r, c in EYES:
        for dr in (0, 1):
            for dc in range(3):
                colour = (PALETTE["y"] if dr == 0 else PALETTE["k"]) if blink else                     PALETTE["k"] if dc == pupil else PALETTE["e"]
                put(r + dr, CAT_X + c + dc, colour)

    # The thread: from under the paws along the floor to the ball, with some slack.
    for c in range(CAT_X + 30, x + 1):
        put(H - 1 if (c // 4) % 2 else H - 2, c, YARN)
    top = H - len(BALL) - hop
    for r, row in enumerate(BALL):
        for c, ch in enumerate(row):
            if ch != ".":
                put(top + r, x + c, {"x": YARN, "s": SHADE, "h": LIGHT}[ch])
    turn = (f if (f % LOOP) < 18 else -f) // 2 % len(STRANDS)
    for r, c in STRANDS[turn]:
        put(top + r, x + c, STRAND)
    return grid


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


SIDE = 26                   # columns the wordmark needs next to the scene


def banner(f: int, label: str, blink: bool = False, width: int = 120, line: str = "") -> Text:
    """The cheetah at frame `f`, the wordmark, the version and the model on its right (under it when
    the terminal is narrow), and `line` below."""
    art = halfblocks(frame(f, blink)).split("\n")
    room = width - W - 3
    beside = room >= SIDE
    side: dict[int, Text] = {}
    if beside:
        model = label if len(label) <= room else label[:max(0, room - 1)] + "…"
        side = {3: Text(WORDMARK[0], style=f"bold {AMBER}"),
                4: Text(WORDMARK[1], style=f"bold {AMBER}"),
                6: Text.assemble(("agent", HOT), ("  v" + __version__, DIM)),
                7: Text(model, style=TEXT)}
    out = Text(no_wrap=True, overflow="crop")
    for i in range(H // 2):
        row = art[i] if i < len(art) else Text()
        out.append_text(row)
        if i in side:
            out.append(" " * (W - row.cell_len + 3))
            out.append_text(side[i])
        out.append("\n")
    if not beside:
        out.append_text(Text.assemble(("MAFDET agent", f"bold {AMBER}"), ("  v" + __version__ + "  ", DIM),
                                      (label, TEXT)))
        out.append("\n")
    if line:
        out.append("\n")
        out.append(line, style=f"italic {DIM}")
    out.rstrip()
    return out


def height(width: int, line: bool = True) -> int:
    """The lines `banner` takes at this width."""
    return H // 2 + (0 if width - W - 3 >= SIDE else 1) + (2 if line else 0)


__all__ = ["banner", "frame", "height", "quip", "FPS"]
