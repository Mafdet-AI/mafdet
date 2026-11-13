"""The cheetah at the top of `mafdet agent`: the scene, the name, the banner and the line under it."""
import random

from mafdet import __version__
from mafdet.agent.banner import (EYES, H, LOOP, NAME, PALETTE, QUIPS, STRAND, W, X, YARN, banner, frame, height,
                                 quip)


def test_every_frame_fits_and_has_the_cheetah_and_the_ball():
    for f in range(LOOP):
        grid = frame(f)
        assert len(grid) == H and all(len(row) == W for row in grid)
        flat = [c for row in grid for c in row]
        assert YARN in flat and STRAND in flat and PALETTE["p"] in flat


def test_eyes_open_with_a_light_and_shut_for_a_blink_or_a_bat():
    def lights(grid):
        return sum(grid[r][X + c] == PALETTE["w"] for r, c in EYES)
    assert lights(frame(8)) == 2
    assert lights(frame(8, blink=True)) == 0
    assert lights(frame(0)) == 0                        # batting the ball, pleased


def test_the_ball_rolls_away_and_back():
    def ball_left(f):
        return min(c for row in frame(f) for c, colour in enumerate(row) if colour == STRAND)
    lefts = [ball_left(f) for f in range(LOOP)]
    assert max(lefts) - min(lefts) >= 8


def test_the_name_is_drawn_gold_to_coral():
    assert len(NAME) == 8
    colours = [c for row in NAME for c in row if c]
    assert len(set(colours)) > 5


def test_banner_shows_version_model_and_line_and_fits_the_width():
    text = banner(0, "Qwen3-14B-Q4_K_M", width=120, line="Miao è il mio Hello World.").plain
    assert "v" + __version__ in text and "Qwen3-14B-Q4_K_M" in text and "Hello World" in text
    assert text.count("\n") + 1 == height(120)
    narrow = banner(0, "a-very-long-model-name " * 5, width=50, line="x").plain
    assert "mafdet agent" in narrow and narrow.count("\n") + 1 == height(50)


def test_quip_follows_the_language_and_falls_back_to_english():
    assert len(QUIPS["it"]) == len(QUIPS["en"]) >= 20
    assert quip("it", random.Random(1)) in QUIPS["it"]
    assert quip("de", random.Random(1)) in QUIPS["en"]
