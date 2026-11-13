# The phonk edit of Mafdet Agent

A vertical video (1080x1920, 30 fps, about 45 s) that shows how `mafdet agent` works, cut to a phonk
track. It is made with [Remotion](https://www.remotion.dev) (React + TypeScript) in `video/`, and every
cut comes from the beats of the track: no frame number is written by hand.

    video/src/beats.json          the time grid, written by tools/analyze_audio.py (librosa)
    video/src/storyboard.json     which scene plays between which beats (storyboard.md says what each shows)
    video/src/timing.ts           seconds and beats to frames; scenes/sceneTiming.ts for the inside of a scene
    video/src/components/         PunchText, GlitchTransition, FlashCut, ShakeOnBeat, CodeReveal,
                                  FlowDiagram, GrainOverlay
    video/src/scenes/             one file per scene (S01 ... S15)
    video/src/Edit.tsx            the scenes on the grid, the track under them, grain on top
    docs/mafdet_brief.md          the facts the video may state, with their sources
    out/mafdet_phonk_edit.mp4     the final video;  out/preview_frames/  a frame every half second

The track, `out/` and `video/public/audio/` are not in the repository: the music is not ours to publish.

## Render it again

Node 20 or later, from `video/`:

    npm install
    npx remotion render src/index.ts Edit ../out/_raw.mp4 --codec=h264 --crf=14

Then the post (grain, H.264 + AAC), from the repository's root; ffmpeg comes with the project:

    video/node_modules/ffmpeg-static/ffmpeg.exe -y -i out/_raw.mp4 \
      -vf "noise=alls=4:allf=t,format=yuv420p" -c:v libx264 -preset slow -crf 20 \
      -maxrate 14M -bufsize 28M \
      -c:a aac -b:a 256k -movflags +faststart out/mafdet_phonk_edit.mp4

To work on it with a live preview: `npx remotion studio src/index.ts`. One frame:
`npx remotion still src/index.ts Edit frame.png --frame=245`.

## Change the track

1. Put the new track in `assets/music/` (alone, or pass its path).
2. Once, the analysis environment: `python -m venv video/.venv-audio`, then
   `video/.venv-audio/Scripts/pip install librosa soundfile numpy`.
3. `video/.venv-audio/Scripts/python video/tools/analyze_audio.py [track] [--start S] [--length L] [--phase 0-3]`.
   It finds tempo, beats, bars and the drop, picks an excerpt of 30 to 45 seconds around the drop, and
   writes `video/src/beats.json` and `video/public/audio/track.wav`. It also writes `out/beat_check.wav`,
   the excerpt with a click on every beat (higher on the first of a bar): listen to it, and if the high
   click is not on the "one", run again with another `--phase`.
4. Render again. The scenes follow the new grid by themselves, but the storyboard was written for this
   track's shape: 88 beats, the drop on beat 16, a break before beat 48, the last hit on beat 80. A track
   with another shape needs `video/src/storyboard.json` moved to its beats (the scenes take theirs from
   there and from `sceneTiming(fromBeat, toBeat)` at the top of each file).

## Change what it says

Texts are in the scenes, one file each. What the video may claim is in `docs/mafdet_brief.md`, with a
list of what must not be said (unmeasured or untried things): keep to it, and keep the conditions of a
number next to the number.
