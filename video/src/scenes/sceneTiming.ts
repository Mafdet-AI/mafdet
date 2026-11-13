// Timing inside a scene. A scene is mounted in a Sequence that starts on its first beat, so everything
// here is in frames RELATIVE to that beat. Scenes take every frame number from these helpers.
import {beat, beats, f} from "../timing";

/** For the scene that starts on beat `fromBeat` and ends before `toBeat` (as in storyboard.json). */
export const sceneTiming = (fromBeat: number, toBeat: number) => {
  const start = beat(fromBeat);
  const end = toBeat >= beats.beats.length ? beats.durationInFrames : beat(toBeat);
  return {
    frames: end - start,
    /** frame, relative to the scene, of global beat `b` plus a fraction of a beat (at(17, 0.5) = half a beat after 17) */
    at: (b: number, fraction = 0) => {
      const i = Math.max(0, Math.min(beats.beats.length - 1, b));
      const next = i + 1 < beats.beats.length ? beats.beats[i + 1] : beats.beats[i] + 60 / beats.bpm;
      return f(beats.beats[i] + (next - beats.beats[i]) * fraction) - start;
    },
    /** relative frames of every beat of the scene (ShakeOnBeat's `hits`), every `every`-th */
    hits: (every = 1) =>
      beats.beats.map(f).filter((x) => x >= start && x < end).filter((_, i) => i % every === 0).map((x) => x - start),
    /** relative frames of the onsets inside the scene */
    onsets: () => beats.onsets.map(f).filter((x) => x >= start && x < end).map((x) => x - start),
    /** frames one beat lasts here */
    beatFrames: Math.round((60 / beats.bpm) * beats.fps),
  };
};
