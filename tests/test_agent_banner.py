"""The cheetah at the top of `mafdet agent`: the scene, the wordmark, the banner and the line under it."""
import random

from mafdet import __version__
from mafdet.agent.banner import EYES, H, LOOP, NAME, PALETTE, QUIPS, W, banner, frame, height, quip
from mafdet.identity import PAPER


def test_every_frame_fits_and_has_the_cheetah_and_the_ball():
    for f in range(LOOP):
        grid = frame(f)
        assert len(grid) == H and all(len(row) == W for row in grid)
        flat = [c for row in grid for c in row]
        assert PALETTE["K"] in flat and PALETTE["Y"] in flat


def test_eyes_open_and_shut_for_a_blink():
    def eyes(grid):
        return [grid[r][c] for r, c in EYES]
    assert eyes(frame(5)) == [PALETTE["E"]] * 2
    assert eyes(frame(5, blink=True)) == [PALETTE["S"]] * 2


def test_the_paw_taps_the_ball_which_rolls_off_and_back():
    def ball_left(f):
        return min(c for row in frame(f) for c, colour in enumerate(row) if colour == PALETTE["Y"])
    lefts = [ball_left(f) for f in range(LOOP)]
    assert lefts[0] == lefts[-1] and max(lefts) - min(lefts) >= 6
    assert frame(28) != frame(10)                       # the paw up


def test_the_wordmark_is_the_identitys_with_even_stems():
    assert len(NAME) == 8 and {c for row in NAME for c in row} == {None, PAPER}
    left_stem = [row[0] for row in NAME]                # the M's first stem, top to bottom
    assert all(left_stem) and not any(row[1] for row in NAME[3:])


def test_banner_shows_version_model_and_line_and_fits_the_width():
    text = banner(0, "Qwen3-14B-Q4_K_M", width=120, line="Miao è il mio Hello World.").plain
    assert "v" + __version__ in text and "Qwen3-14B-Q4_K_M" in text and "Hello World" in text
    assert text.count("\n") + 1 == height(120)
    narrow = banner(0, "a-very-long-model-name " * 5, width=60, line="x").plain
    assert "MAFDET  agent" in narrow and narrow.count("\n") + 1 == height(60)


def test_quip_follows_the_language_and_falls_back_to_english():
    assert len(QUIPS["it"]) == len(QUIPS["en"]) >= 20
    assert quip("it", random.Random(1)) in QUIPS["it"]
    assert quip("de", random.Random(1)) in QUIPS["en"]
