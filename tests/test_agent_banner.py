"""The kitten at the top of `mafdet agent`: the scene, and the banner with the wordmark, version and model."""
from mafdet import __version__
from mafdet.agent.banner import EYE, H, LOOP, STRAND, W, YARN, banner, frame


def test_every_frame_fits_and_has_the_kitten_and_the_ball():
    for f in range(LOOP):
        grid = frame(f)
        assert len(grid) == H and all(len(row) == W for row in grid)
        flat = [c for row in grid for c in row]
        assert flat.count(EYE) == 2
        assert YARN in flat
    assert EYE not in [c for row in frame(3, blink=True) for c in row]


def test_the_ball_rolls_and_the_eyes_follow_it():
    def ball_left(f):
        return min(c for row in frame(f) for c, colour in enumerate(row) if colour == STRAND)   # strands are only on the ball

    def eyes(f):
        return [c for row in frame(f) for c, colour in enumerate(row) if colour == EYE]
    lefts = [ball_left(f) for f in range(LOOP)]
    assert max(lefts) - min(lefts) >= 8
    assert eyes(10) != eyes(0)


def test_banner_shows_version_and_model_and_crops_to_the_width():
    text = banner(0, "Qwen3-14B-Q4_K_M", width=96).plain
    assert "v" + __version__ in text and "Qwen3-14B-Q4_K_M" in text
    assert text.count("\n") == H // 2 - 1
    narrow = banner(0, "a-very-long-model-name " * 5, width=70).plain
    assert all(len(line) <= 70 for line in narrow.splitlines())
