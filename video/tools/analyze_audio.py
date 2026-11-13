"""Analyse the track of the phonk edit and write the time grid every animation uses.

    python video/tools/analyze_audio.py [path-to-track] [--start S] [--length L] [--phase 0-3]

Defaults: the first file in assets/music, automatic choice of the excerpt. --start and --length are in
seconds of the full track; they are snapped to downbeats (whole bars).

Writes:
    video/src/beats.json           the grid (seconds from the start of the clip)
    video/public/audio/track.wav   the clip, 44.1 kHz stereo 16 bit, 15 ms fade in, 400 ms fade out
    out/beat_check.wav             the clip with a click on every beat (higher on downbeats), to listen
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
MUSIC = ROOT / "assets" / "music"
FFMPEG = ROOT / "video" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
OUT_JSON = ROOT / "video" / "src" / "beats.json"
OUT_WAV = ROOT / "video" / "public" / "audio" / "track.wav"
OUT_CHECK = ROOT / "out" / "beat_check.wav"

FPS = 30
SR_OUT = 44100
SR = 22050          # analysis rate
HOP = 512
BEATS_PER_BAR = 4
BPM_RANGE = (90.0, 160.0)     # a slowed montagem sits around 110-140; outside this we fold the octave
CLIP_MIN, CLIP_MAX = 30.0, 45.0
LEAD_MIN, LEAD_MAX, LEAD_IDEAL = 8.0, 12.0, 10.0
DROP_MIN = 16.0
FADE_IN, FADE_OUT = 0.015, 0.400


def log(*a):
    print(*a, flush=True)


def db(x):
    return 10.0 * np.log10(np.maximum(x, 1e-12))


# ---------------------------------------------------------------- loading

def load_stereo(path: Path) -> np.ndarray:
    """The whole track at 44.1 kHz, shape (2, n). Falls back to ffmpeg if librosa cannot decode."""
    try:
        y, _ = librosa.load(str(path), sr=SR_OUT, mono=False)
    except Exception as e:  # noqa: BLE001
        log(f"librosa could not decode ({e!r}); converting with ffmpeg")
        tmp = Path(tempfile.gettempdir()) / "analyze_audio_decoded.wav"
        subprocess.run([str(FFMPEG), "-y", "-v", "error", "-i", str(path), "-ar", str(SR_OUT), "-ac", "2", str(tmp)],
                       check=True)
        y, _ = librosa.load(str(tmp), sr=SR_OUT, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    return y[:2].astype(np.float32)


# ---------------------------------------------------------------- beats

def attack_curve(sig: np.ndarray, frame: int = 256):
    """A fine (1.45 ms) attack function: the rise of a short RMS, lightly smoothed."""
    hop = 32
    r = librosa.feature.rms(y=sig, frame_length=frame, hop_length=hop, center=True)[0]
    a = np.maximum(np.diff(r, prepend=r[0]), 0.0)
    k = np.hanning(9)
    return np.convolve(a, k / k.sum(), mode="same"), hop / SR


def exact_period(att: np.ndarray, dt: float, period: float) -> float:
    """Refine the beat period on the autocorrelation of the attack curve, at lags of 1, 2, 4 ... 64 beats:
    each longer lag divides the error. Produced music has a fixed tempo, so the long lags are sharp."""
    a = att - att.mean()
    n = len(a)
    ac = np.fft.irfft(np.abs(np.fft.rfft(a, 2 * n)) ** 2)[:n]
    mult = 1
    while mult <= 64 and mult * period < 0.4 * n * dt:
        c = int(round(mult * period / dt))
        w = int(0.012 * np.sqrt(mult) / dt) + 3
        seg = ac[c - w:c + w + 1] / (n - np.arange(c - w, c + w + 1))
        j = int(np.argmax(seg))
        d = 0.0
        if 0 < j < len(seg) - 1:
            den = seg[j - 1] - 2 * seg[j] + seg[j + 1]
            d = 0.5 * (seg[j - 1] - seg[j + 1]) / den if den else 0.0
        period = (c - w + j + d) * dt / mult
        mult *= 2
    return float(period)


def comb(att: np.ndarray, dt: float, period: float):
    """The attack curve folded over one beat: (phases, mean attack at each phase)."""
    nb = int(round(period / dt))
    t = np.arange(len(att)) * dt
    idx = np.floor((t % period) / period * nb).astype(int) % nb
    fold = np.bincount(idx, weights=att, minlength=nb) / np.maximum(np.bincount(idx, minlength=nb), 1)
    return np.arange(nb) * period / nb, fold


def track_beats(y: np.ndarray, yp: np.ndarray):
    """(beats of the whole track, bpm, doubts). librosa's tracker on the percussive part gives the tempo;
    the grid is then made exact: period from the autocorrelation, phase from the kick (low band)."""
    from scipy import signal

    total = len(y) / SR
    notes = []
    oenv = librosa.onset.onset_strength(y=yp, sr=SR, hop_length=HOP)
    tempo, frames = librosa.beat.beat_track(onset_envelope=oenv, sr=SR, hop_length=HOP, start_bpm=120.0)
    tempo = float(np.atleast_1d(tempo)[0])
    bt = librosa.frames_to_time(frames, sr=SR, hop_length=HOP)
    log(f"beat_track: {tempo:.2f} BPM, {len(bt)} beats, interval std {np.std(np.diff(bt)) * 1000:.1f} ms")

    # half/double-tempo: fold into the plausible range
    period = float(np.median(np.diff(bt)))
    while 60.0 / period < BPM_RANGE[0]:
        period /= 2
        notes.append(f"tempo doubled to {60 / period:.1f} BPM (the tracker locked on the half tempo)")
    while 60.0 / period > BPM_RANGE[1]:
        period *= 2
        notes.append(f"tempo halved to {60 / period:.1f} BPM (the tracker locked on the double tempo)")

    att, dt = attack_curve(yp)
    period = exact_period(att, dt, period)
    bpm = 60.0 / period
    log(f"exact period {period:.6f} s = {bpm:.3f} BPM")

    # phase: where the kick falls. Low band first (the kick), then the wide-band transient next to it.
    low = signal.sosfiltfilt(signal.butter(4, 150, "low", fs=SR, output="sos"), y)
    att_low, _ = attack_curve(low, frame=512)
    ph, fold_low = comb(att_low, dt, period)
    k0 = int(np.argmax(fold_low))
    contrast = float(fold_low[k0] / (np.median(fold_low) + 1e-12))
    _, fold = comb(att, dt, period)
    nb = len(ph)
    near = (np.arange(-int(0.015 / dt), int(0.015 / dt) + 1) + k0) % nb
    k1 = int(near[np.argmax(fold[near])])
    phase = float(ph[k1])
    # the tracker's own phase, for the record
    z = np.exp(2j * np.pi * bt / period).mean()
    tr_phase = float((np.angle(z) % (2 * np.pi)) / (2 * np.pi) * period)
    off = ((tr_phase - phase + period / 2) % period) - period / 2
    log(f"kick phase {phase * 1000:.1f} ms (low-band comb contrast {contrast:.1f}x); "
        f"librosa's beats sit {off * 1000:+.0f} ms from the kick (coherence {abs(z):.2f})")
    if contrast < 3.0:
        notes.append(f"the low band does not mark the beat clearly (contrast {contrast:.1f}x): the phase of "
                     "librosa's tracker is used instead of the kick")
        phase = tr_phase
    elif abs(off) > 0.06:
        notes.append(f"librosa's tracker put the beats {off * 1000:+.0f} ms away from the kick "
                     f"({off / period:+.2f} beat, it followed the syncopated claps/vocal chops); the grid is "
                     "aligned on the kick, which is four-on-the-floor in the drop")

    # the same check at the octave: is there a kick on the off-beat too?
    k_off = (k0 + nb // 2) % nb
    ratio = float(fold_low[(np.arange(-3, 4) + k_off) % nb].max() / fold_low[k0])
    log(f"octave check: low-band attack on the off-beat is {ratio:.2f} of the one on the beat")
    if ratio > 0.6:
        notes.append(f"the off-beat carries {ratio:.2f} of the kick energy: {bpm * 2:.1f} BPM is also plausible")

    # is the grid steady along the track? the kick phase of each 16-beat stretch against the global one
    seg = int(round(16 * period / dt))
    devs = []
    for i in range(0, len(att_low) - seg, seg):
        _, f = comb(att_low[i:i + seg], dt, period)
        f = np.roll(f, int(round(((i * dt) % period) / period * nb)))
        if f.max() > 0.5 * fold_low[k0]:                     # only where there is a kick
            j = int(np.argmax(f))
            devs.append((((j - k0 + nb // 2) % nb) - nb // 2) * period / nb)
    if devs:
        devs = np.array(devs)
        log(f"grid stability: kick within {np.abs(devs).max() * 1000:.1f} ms of the grid over "
            f"{len(devs)} stretches of 16 beats (std {devs.std() * 1000:.1f} ms)")
        if np.abs(devs).max() > 0.025:
            notes.append(f"the kick drifts up to {np.abs(devs).max() * 1000:.0f} ms from a fixed-tempo grid")

    beats = phase + period * np.arange(int((total - phase) / period) + 1)
    return beats[beats < total], bpm, notes


# ---------------------------------------------------------------- bars, drop

def band_energy(y: np.ndarray):
    """Per analysis frame: low-band (< 150 Hz) power, and overall power (RMS squared)."""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    f = librosa.fft_frequencies(sr=SR, n_fft=2048)
    low = S[f < 150].sum(0)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0] ** 2
    n = min(len(low), len(rms))
    return low[:n], rms[:n]


def mean_between(x: np.ndarray, a: float, b: float) -> float:
    i, j = int(round(a * SR / HOP)), int(round(b * SR / HOP))
    i, j = max(i, 0), min(max(j, i + 1), len(x))
    return float(x[i:j].mean()) if j > i else 0.0


def downbeat_phase(beats: np.ndarray, low: np.ndarray, rms: np.ndarray, y: np.ndarray):
    """Which of the 4 beat phases starts the bar. Three readings, returned for the caller to weigh:
    - low: the low-frequency energy right on the beats of each phase (normalised to the best);
    - anchor: the phase of the first audible beat of the track (produced tracks start on a bar), or None;
    - runs: on which phase the full-level passages (16 beats or more) begin."""
    kick = np.array([mean_between(low, t, t + 0.12) for t in beats])
    low_score = np.array([kick[k::BEATS_PER_BAR].mean() for k in range(BEATS_PER_BAR)])
    low_score = low_score / low_score.max()

    anchor = None
    loud = np.flatnonzero(np.abs(y) > 0.02 * np.abs(y).max())
    if len(loud):
        first = loud[0] / SR
        i = int(np.argmin(np.abs(beats - first)))
        if abs(beats[i] - first) < 0.08:
            anchor = i % BEATS_PER_BAR

    period = float(np.median(np.diff(beats)))
    level = np.array([db(mean_between(low, t, t + period)) + db(mean_between(rms, t, t + period)) for t in beats])
    hot = level >= np.percentile(level, 90) - 2.5
    runs = np.zeros(BEATS_PER_BAR, dtype=int)
    i = 0
    while i < len(hot):
        if hot[i]:
            j = i
            while j < len(hot) and hot[j]:
                j += 1
            if j - i >= 16 and i > 0:
                runs[i % BEATS_PER_BAR] += 1
            i = j
        else:
            i += 1
    return low_score, anchor, runs


def find_drop(downs: np.ndarray, low: np.ndarray, rms: np.ndarray, bar: float, total: float):
    """Score every downbeat: the jump of low-band + overall level between the 4 bars before and the 4 bars
    after, where 'after' is the quietest of those 4 bars (so the jump has to last)."""
    ref = np.percentile([db(mean_between(low, d, d + bar)) for d in downs if d + bar <= total], 95)
    out = []
    for d in downs:
        if d - 4 * bar < -1e-6 or d + 4 * bar > total:
            continue
        lo_b, rm_b = db(mean_between(low, d - 4 * bar, d)), db(mean_between(rms, d - 4 * bar, d))
        lo_a = min(db(mean_between(low, d + k * bar, d + (k + 1) * bar)) for k in range(4))
        rm_a = min(db(mean_between(rms, d + k * bar, d + (k + 1) * bar)) for k in range(4))
        score = (lo_a - lo_b) + (rm_a - rm_b) - max(0.0, ref - lo_a)   # a drop is at the track's full level
        out.append((float(score), float(d), float(lo_a - lo_b), float(rm_a - rm_b)))
    out.sort(reverse=True)
    return out


def drop_end(drop: float, downs: np.ndarray, low: np.ndarray, bar: float, total: float):
    """The first downbeat after the drop where the low band falls away (6 dB) for two bars; None if never."""
    level = db(mean_between(low, drop, drop + 4 * bar))
    for d in downs[downs > drop + 4 * bar - 1e-6]:
        if d + 2 * bar > total:
            break
        if db(mean_between(low, d, d + 2 * bar)) < level - 6.0:
            return float(d)
    return None


# ---------------------------------------------------------------- the clip

def choose_clip(drop: float, end_of_drop, bar: float, total: float):
    """(start, end, why). Starts on a downbeat 8-12 s before the drop, at least 16 s of drop, ends on a
    downbeat, 30-45 s in all."""
    cands = []
    for nb in range(1, 12):
        lead = nb * bar
        if drop - lead < -1e-6:
            break
        for na in range(1, 40):
            length = lead + na * bar
            if drop + na * bar > total + 1e-6:
                break
            outro = max(1, round(4.0 / bar)) * bar
            drop_len = na * bar - outro
            pen = 0.0
            pen += 10 * max(0, LEAD_MIN - lead, lead - LEAD_MAX) + 0.3 * abs(lead - LEAD_IDEAL)
            pen += 10 * max(0, CLIP_MIN - length, length - CLIP_MAX)
            pen += 10 * max(0, DROP_MIN - drop_len)
            why = []
            if end_of_drop is not None and abs((drop + na * bar - outro) - end_of_drop) < 1e-3:
                pen -= 3.0      # the outro is the breakdown after the drop: the clip ends by itself
                why.append("the outro is the passage right after the drop, where the bass falls away")
            elif na % 4 == 0:
                pen -= 1.0      # ends on a 4-bar phrase
                why.append("ends on a 4-bar phrase of the drop")
            cands.append((pen, nb, na, why))
    if not cands:
        raise SystemExit("the track is too short around the drop to cut a clip")
    pen, nb, na, why = min(cands, key=lambda c: (c[0], -c[2]))
    start, end = drop - nb * bar, drop + na * bar
    lead, length = nb * bar, (nb + na) * bar
    if not (LEAD_MIN <= lead <= LEAD_MAX):
        why.append(f"the lead before the drop is {lead:.1f} s, outside 8-12 s: the track does not allow better")
    if not (CLIP_MIN <= length <= CLIP_MAX):
        why.append(f"the clip is {length:.1f} s, outside 30-45 s: the track does not allow better")
    return start, end, why


def snap(t: float, grid: np.ndarray) -> float:
    return float(grid[np.argmin(np.abs(grid - t))])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track", nargs="?", help="default: the first file in assets/music")
    ap.add_argument("--start", type=float, help="start of the clip in the full track, seconds (snapped to a downbeat)")
    ap.add_argument("--length", type=float, help="length of the clip, seconds (snapped to whole bars)")
    ap.add_argument("--phase", type=int, choices=range(BEATS_PER_BAR),
                    help="force which beat (0-3, counted from the track's first beat) starts the bars")
    args = ap.parse_args()

    if args.track:
        path = Path(args.track)
    else:
        files = sorted(p for p in MUSIC.iterdir() if p.is_file() and not p.name.startswith("."))
        if not files:
            raise SystemExit(f"no track in {MUSIC}")
        path = files[0]
    log(f"track: {path.name}")

    stereo = load_stereo(path)
    total = stereo.shape[1] / SR_OUT
    y = librosa.resample(stereo.mean(0), orig_sr=SR_OUT, target_sr=SR)
    log(f"length: {total:.2f} s")
    _, yp = librosa.effects.hpss(y)

    doubts: list[str] = []
    beats, bpm, notes = track_beats(y, yp)
    doubts += notes
    period = 60.0 / bpm
    bar = period * BEATS_PER_BAR

    low, rms = band_energy(y)
    low_score, anchor, runs = downbeat_phase(beats, low, rms, y)
    phase = int(np.argmax(low_score))
    log(f"downbeat: low-band energy per beat phase {np.round(low_score, 3).tolist()}, first audible beat on "
        f"phase {anchor}, full-level passages starting on each phase {runs.tolist()}")
    order = np.sort(low_score)[::-1]
    if args.phase is not None:
        phase = args.phase % BEATS_PER_BAR
        log(f"downbeat phase {phase}, given on the command line")
    elif order[1] > 0.9 * order[0]:
        # a kick on every beat: the low band cannot tell the bar. The structure decides.
        structural = anchor if anchor is not None else (int(np.argmax(runs)) if runs.sum() else phase)
        if structural != phase:
            doubts.append(
                f"downbeats: the low band does not decide (energy per phase {np.round(low_score, 2).tolist()}, "
                f"a kick on every beat; phase {phase} is ahead by little). "
                f"Phase {structural} is used: the track's first beat is on it"
                + (f", and {int(runs[structural])} of {int(runs.sum())} full-level passages start on it"
                   if runs.sum() else "")
                + f". To force another: --phase {phase}")
        phase = structural
        if runs.sum() and int(np.argmax(runs)) != phase:
            doubts.append(f"full-level passages start mostly on phase {int(np.argmax(runs))}, not on {phase}")
        log(f"downbeat phase {phase} (low band undecided, structure used)")
    else:
        log(f"downbeat phase {phase} (low band)")
    downs_all = beats[phase::BEATS_PER_BAR]

    ranking = find_drop(downs_all, low, rms, bar, total)
    if not ranking:
        raise SystemExit("the track is too short to look for a drop")
    log("drop candidates (score, time, low jump dB, overall jump dB):")
    for s, d, lj, rj in ranking[:6]:
        log(f"   {s:6.1f}  {d:8.3f} s   low {lj:+5.1f}   overall {rj:+5.1f}")

    manual = args.start is not None or args.length is not None
    if manual:
        start = snap(args.start if args.start is not None else 0.0, downs_all)
        n_bars = max(1, round((args.length if args.length is not None else 40.0) / bar))
        end = min(start + n_bars * bar, snap(total, downs_all[downs_all <= total]))
        inside = [r for r in ranking if start + bar - 1e-6 <= r[1] <= end - 2 * bar + 1e-6]
        if not inside:
            raise SystemExit("no drop candidate inside the requested excerpt")
        best = inside[0]
        others = [r for r in inside if abs(r[1] - best[1]) > 8 * bar - 1e-6]
        why = ["excerpt given on the command line"]
    else:
        best = ranking[0]
        others = [r for r in ranking if abs(r[1] - best[1]) > 8 * bar - 1e-6]
    drop_abs = best[1]
    runner = others[0] if others else None
    if runner:
        log(f"runner-up drop: {runner[1]:.3f} s (score {runner[0]:.1f} against {best[0]:.1f})")
        if runner[0] > 0.8 * best[0]:
            doubts.append(f"the drop is ambiguous: {runner[1]:.2f} s scores {runner[0]:.1f} against {best[0]:.1f}")
    eod = drop_end(drop_abs, downs_all, low, bar, total)
    log(f"drop at {drop_abs:.3f} s; it lasts until {eod if eod is None else round(eod, 3)} s")
    if not manual:
        start, end, why = choose_clip(drop_abs, eod, bar, total)
    log(f"clip: {start:.3f} -> {end:.3f} s of the track ({end - start:.2f} s). " + "; ".join(why))

    # ---- cut, in samples, so the wav and the json agree exactly
    s0, s1 = int(round(start * SR_OUT)), min(int(round(end * SR_OUT)), stereo.shape[1])
    clip = stereo[:, s0:s1].copy()
    n = clip.shape[1]
    duration = n / SR_OUT
    frames = int(round(duration * FPS))
    c0 = s0 / SR_OUT

    eps = 1e-3
    cb = beats - c0
    cb = cb[(cb > -eps) & (cb < duration - eps)]
    cb[0] = max(cb[0], 0.0)
    cd = downs_all - c0
    cd = cd[(cd > -eps) & (cd < duration - eps)]
    cd[0] = max(cd[0], 0.0)
    drop = drop_abs - c0

    onset_frames = librosa.onset.onset_detect(y=yp, sr=SR, hop_length=256, backtrack=False, units="frames")
    onsets = librosa.frames_to_time(onset_frames, sr=SR, hop_length=256) - c0
    onsets = onsets[(onsets >= 0) & (onsets < duration - eps)]

    # ---- sections
    hook_end = float(next((d for d in cd if d >= 1.9 and d < drop - eps), cd[min(1, len(cd) - 1)]))
    tail = [d for d in cd if d > drop + eps]
    outro_start = float(min(tail, key=lambda d: abs((duration - d) - 4.0))) if tail else drop
    sections = [("hook", 0.0, hook_end), ("build", hook_end, drop), ("drop", drop, outro_start),
                ("outro", outro_start, duration)]
    if outro_start - drop < DROP_MIN:
        doubts.append(f"the drop section is only {outro_start - drop:.1f} s")
    lead_db = db(mean_between(low, start, drop_abs)) - db(mean_between(low, drop_abs, drop_abs + 4 * bar))
    log(f"the lead-in sits {lead_db:+.1f} dB (low band) from the drop")

    # ---- energy: one value per video frame, RMS over about 2 frames, lightly smoothed, normalised
    mono = clip.mean(0)
    spf = SR_OUT / FPS
    e = np.empty(frames)
    for i in range(frames):
        a, b = int(round((i - 0.5) * spf)), int(round((i + 1.5) * spf))
        seg = mono[max(a, 0):min(b, n)]
        e[i] = np.sqrt(np.mean(seg ** 2)) if len(seg) else 0.0
    e = np.convolve(np.pad(e, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    e = np.clip(e / max(e.max(), 1e-9), 0.0, 1.0)

    # ---- export the clip
    fi, fo = int(FADE_IN * SR_OUT), int(FADE_OUT * SR_OUT)
    out = clip.copy()
    out[:, :fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    out[:, -fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    out = np.clip(out, -1.0, 1.0)
    OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(OUT_WAV), out.T, SR_OUT, subtype="PCM_16")

    r4 = lambda v: round(float(v), 4)  # noqa: E731
    data = {
        "source": path.name,
        "bpm": round(float(bpm), 3),
        "fps": FPS,
        "clip": {"start": r4(c0), "end": r4(c0 + duration)},
        "duration": r4(duration),
        "durationInFrames": frames,
        "beats": [r4(t) for t in cb],
        "downbeats": [r4(t) for t in cd],
        "onsets": [r4(t) for t in onsets],
        "drop": r4(drop),
        "sections": [{"name": nme, "start": r4(a), "end": r4(b)} for nme, a, b in sections],
        "energy": [r4(v) for v in e],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, separators=(", ", ": ")) + "\n", encoding="utf-8")

    # ---- click track, to check by ear
    def click(freq, ms=30):
        t = np.arange(int(SR_OUT * ms / 1000)) / SR_OUT
        return (np.sin(2 * np.pi * freq * t) * np.exp(-t * 120)).astype(np.float32)
    chk = out * 0.6
    hi, lo_c = click(1760.0), click(880.0)
    downset = set(data["downbeats"])
    for t in data["beats"]:
        c = hi if t in downset else lo_c
        i = int(round(t * SR_OUT))
        j = min(i + len(c), n)
        chk[:, i:j] += 0.8 * c[: j - i]
    OUT_CHECK.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(OUT_CHECK), np.clip(chk, -1, 1).T, SR_OUT, subtype="PCM_16")

    problems = verify()
    log("")
    log(f"BPM {data['bpm']}   clip {data['clip']['start']} -> {data['clip']['end']} s   duration {data['duration']} s"
        f"   {frames} frames   drop {data['drop']} s")
    for sct in data["sections"]:
        log(f"   {sct['name']:<6}{sct['start']:8.4f} -> {sct['end']:8.4f}")
    log(f"{len(data['beats'])} beats, {len(data['downbeats'])} downbeats, {len(data['onsets'])} onsets")
    log("doubts: " + ("; ".join(doubts) if doubts else "none"))
    log(f"wrote {OUT_JSON}\n      {OUT_WAV}\n      {OUT_CHECK}")
    if problems:
        sys.exit(1)


def verify() -> list[str]:
    """Check beats.json and track.wav against the contract. Prints, and returns the list of problems."""
    d = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    bad = []

    def check(ok, msg):
        log(("  ok   " if ok else "  FAIL ") + msg)
        if not ok:
            bad.append(msg)

    log("verify:")
    b = np.array(d["beats"])
    iv = np.diff(b)
    check(bool((iv > 0).all()), f"beats strictly increasing; interval mean {iv.mean() * 1000:.2f} ms, "
                                f"std {iv.std() * 1000:.3f} ms (60/bpm = {60000 / d['bpm']:.2f} ms)")
    check(iv.std() < 0.02 and abs(iv.mean() - 60 / d["bpm"]) < 0.005, "steady interval that matches the bpm")
    idx = [d["beats"].index(x) if x in d["beats"] else -1 for x in d["downbeats"]]
    check(all(i >= 0 for i in idx) and all(j - i == BEATS_PER_BAR for i, j in zip(idx, idx[1:])),
          "downbeats are a subset of beats, every 4th beat")
    check(d["drop"] in d["downbeats"], "the drop is a downbeat")
    s = d["sections"]
    check([x["name"] for x in s] == ["hook", "build", "drop", "outro"] and s[0]["start"] == 0
          and s[-1]["end"] == d["duration"] and all(a["end"] == c["start"] for a, c in zip(s, s[1:]))
          and all(x["end"] > x["start"] for x in s), "four contiguous sections from 0 to duration")
    check(s[2]["start"] == d["drop"], "section drop starts on the drop")
    check(all(x["start"] in d["downbeats"] for x in s), "every section starts on a downbeat")
    check(d["durationInFrames"] == round(d["duration"] * d["fps"]) and len(d["energy"]) == d["durationInFrames"],
          f"energy has durationInFrames ({d['durationInFrames']}) values")
    check(min(d["energy"]) >= 0 and max(d["energy"]) <= 1 and max(d["energy"]) > 0.99, "energy within 0..1, max 1")
    check(all(0 <= x < d["duration"] for x in d["beats"] + d["onsets"]), "beats and onsets inside the clip")
    check(abs(d["clip"]["end"] - d["clip"]["start"] - d["duration"]) < 2e-4, "clip end - start = duration")
    info = sf.info(str(OUT_WAV))
    check(abs(info.frames / info.samplerate - d["duration"]) < 0.020 and info.samplerate == SR_OUT
          and info.channels == 2 and info.subtype == "PCM_16",
          f"track.wav: {info.frames / info.samplerate:.4f} s, {info.samplerate} Hz, {info.channels} ch, {info.subtype}")
    return bad


if __name__ == "__main__":
    main()
