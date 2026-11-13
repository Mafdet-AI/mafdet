"""The cheetah at the top of `mafdet agent`: the scene, the banner and the line under it."""
import random

from mafdet import __version__
from mafdet.agent.banner import EYES, CAT_X, H, LOOP, PALETTE, QUIPS, STRAND, W, YARN, banner, frame, height, quip


def test_every_frame_fits_and_has_the_cheetah_and_the_ball():
    for f in range(LOOP):
        grid = frame(f)
        assert len(grid) == H and all(len(row) == W for row in grid)
        flat = [c for row in grid for c in row]
        assert flat.count(PALETTE["e"]) == 2 * 2 * 2          # two eyes, 3x2, the pupil a column of them
        assert YARN in flat and STRAND in flat
    shut = frame(3, blink=True)
    assert PALETTE["e"] not in [c for row in shut for c in row]


def test_the_ball_rolls_and_the_eyes_follow_it():
    def ball_left(f):
        return min(c for row in frame(f) for c, colour in enumerate(row) if colour == STRAND)

    def pupils(f):
        r, c = EYES[0]
        return [i for i in range(3) if frame(f)[r][CAT_X + c + i] == PALETTE["k"]]
    lefts = [ball_left(f) for f in range(LOOP)]
    assert max(lefts) - min(lefts) >= 10
    assert pupils(12) != pupils(LOOP - 1)


def test_banner_shows_version_model_and_line_and_fits_the_width():
    text = banner(0, "Qwen3-14B-Q4_K_M", width=120, line="Miao è il mio Hello World.").plain
    assert "v" + __version__ in text and "Qwen3-14B-Q4_K_M" in text and "Hello World" in text
    assert text.count("\n") + 1 == height(120)
    narrow = banner(0, "a-very-long-model-name " * 5, width=80, line="x").plain
    assert "MAFDET agent" in narrow and narrow.count("\n") + 1 == height(80)


def test_quip_follows_the_language_and_falls_back_to_english():
    assert len(QUIPS["it"]) == len(QUIPS["en"]) >= 20
    assert quip("it", random.Random(1)) in QUIPS["it"]
    assert quip("de", random.Random(1)) in QUIPS["en"]
