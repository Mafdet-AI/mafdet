"""The top of `mafdet agent`: a pixel-art kitten playing with a ball of yarn, live, next to the
wordmark, the version and the model.

The scene is a grid of pixels drawn with half blocks (two pixels a character, art.halfblocks). The
kitten sits on the left; the ball rolls toward it, the kitten raises a paw and bats it away, the
ball rolls off to the right trailing its thread and comes back. Its eyes follow the ball, its tail
swishes, now and then it blinks. One frame is a pure function of the frame number (and of when the
blink falls), so the scene is the same in any terminal and can be tested.
"""
from __future__ import annotations

from rich.text import Text

from .. import __version__
from ..identity import AMBER
from ..tui.art import WORDMARK, halfblocks
from ..tui.fx import DIM, HOT, TEXT

FUR, STRIPE, EYE, NOSE = TEXT, DIM, "#120B04", "#E8806A"
YARN, STRAND = "#E8604A", "#9C3422"

W, H = 34, 14               # pixels: 34 columns, 7 lines of text
CAT_X = 4                   # where the kitten's sprite starts (the tail curls to its left)
FPS = 8

# The kitten, sitting and facing right. o fur, s stripe; the eyes, the nose and the paw are drawn apart.
CAT = ["..o.....o..",
       "..oo...oo..",
       "..ooosooo..",
       ".ooooooooo.",
       ".ooooooooo.",
       "..ooooooo..",
       "...ooooo...",
       "..ooosooo..",
       ".ooooosooo.",
       ".oooooooooo",
       ".ossoooooooo",
       ".ooooooooooo",
       ".ooooo.ooo..",
       "..oo...oo..."]
EYES_ROW = 3

# The front paw: resting on the floor, raised, batting forward. (row, column) inside the sprite.
PAW = {"rest": [(12, 9), (12, 10), (13, 9), (13, 10), (13, 11)],
       "up":   [(7, 9), (8, 10), (8, 11), (7, 11), (7, 12)],
       "bat":  [(9, 11), (10, 12), (10, 13), (10, 14), (9, 14)]}

# The tail from the back of the kitten, three positions of a swish: (row, column) left of CAT_X.
TAIL = [[(12, 0), (12, -1), (11, -2), (10, -2), (9, -2), (8, -1)],
        [(12, 0), (12, -1), (12, -2), (11, -3), (10, -3), (9, -3)],
        [(12, 0), (13, -1), (13, -2), (12, -3), (11, -3), (10, -4)]]

# The ball, 6 pixels across, and its strands in four turns of rolling.
BALL = [".xxxx.",
        "xxxxxx",
        "xxxxxx",
        "xxxxxx",
        "xxxxxx",
        ".xxxx."]
STRANDS = [[(1, 1), (2, 2), (3, 3), (4, 4), (1, 4), (4, 1)],
           [(1, 2), (2, 2), (3, 3), (4, 3), (2, 4), (3, 1)],
           [(1, 3), (2, 3), (3, 2), (4, 2), (2, 1), (3, 4)],
           [(1, 4), (2, 3), (3, 2), (4, 1), (1, 1), (4, 4)]]

# The ball's path over one loop, its left edge in pixels: it rolls in, is batted, flies off, slows, rolls back.
NEAR, FAR = 17, 27
LOOP = 40


def _ball_x(f: int) -> tuple[int, int]:
    """(left edge, hop) of the ball at frame `f` of the loop."""
    f %= LOOP
    if f < 6:                                   # batted: fast, slowing down, a little hop
        t = f / 6
        return NEAR + round((FAR - NEAR) * (1 - (1 - t) ** 2)), (1 if f in (1, 2) else 0)
    if f < 14:                                  # resting far away
        return FAR, 0
    t = (f - 14) / (LOOP - 14)                  # rolling back to the kitten
    return FAR - round((FAR - NEAR) * t), 0


def _paw(f: int) -> str:
    f %= LOOP
    if f in (LOOP - 3, LOOP - 2, LOOP - 1):
        return "up"
    if f in (0, 1):
        return "bat"
    return "rest"


def frame(f: int, blink: bool = False) -> list[list[str | None]]:
    """The scene at frame `f` as a grid of colours (None = empty)."""
    grid: list[list[str | None]] = [[None] * W for _ in range(H)]

    def put(r: int, c: int, colour: str):
        if 0 <= r < H and 0 <= c < W:
            grid[r][c] = colour

    for r, row in enumerate(CAT):
        for c, ch in enumerate(row):
            if ch != ".":
                put(r, CAT_X + c, FUR if ch == "o" else STRIPE)
    swish = TAIL[(0, 1, 2, 1)[(f // 3) % 4]]
    for r, c in swish:
        put(r, CAT_X + c, FUR)
    put(swish[-1][0], CAT_X + swish[-1][1], STRIPE)
    for r, c in PAW[_paw(f)]:
        put(r, CAT_X + c, FUR)

    x, hop = _ball_x(f)
    # The eyes on the ball (the nose turns with them), shut for a blink.
    look = 1 if x > NEAR + 4 else 0
    for c in (3 + look, 7 + look):
        put(EYES_ROW, CAT_X + c, STRIPE if blink else EYE)
    put(EYES_ROW + 1, CAT_X + 5 + look, NOSE)

    # The thread: from under the kitten's paw along the floor to the ball, a little slack in it.
    start, end = CAT_X + 12, x
    for c in range(start, end):
        put(H - 1 if (c // 3) % 2 else H - 2, c, YARN)
    top = H - len(BALL) - hop
    turn = (f if (f % LOOP) < 14 else -f) // 2 % len(STRANDS)
    for r, row in enumerate(BALL):
        for c, ch in enumerate(row):
            if ch == "x":
                put(top + r, x + c, YARN)
    for r, c in STRANDS[turn]:
        put(top + r, x + c, STRAND)
    return grid


def banner(f: int, label: str, blink: bool = False, width: int = 80) -> Text:
    """The kitten at frame `f`, and on its right the wordmark, the version and the model."""
    art = halfblocks(frame(f, blink)).split("\n")
    room = max(0, width - W - 4)
    model = label if len(label) <= room else label[:max(0, room - 1)] + "…"
    side: list[Text] = [Text(),
                        Text(WORDMARK[0], style=f"bold {AMBER}"),
                        Text(WORDMARK[1], style=f"bold {AMBER}"),
                        Text(),
                        Text.assemble(("agent", HOT), ("  v" + __version__, DIM)),
                        Text(model, style=TEXT),
                        Text()]
    out = Text(no_wrap=True, overflow="crop")
    for i in range(H // 2):
        line = art[i] if i < len(art) else Text()
        out.append_text(line)
        out.append(" " * (W - line.cell_len + 3))
        if room:
            out.append_text(side[i])
        if i < H // 2 - 1:
            out.append("\n")
    return out


__all__ = ["banner", "frame", "FPS"]
