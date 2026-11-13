// All timing comes from beats.json through here. No frame number is ever written by hand in a scene.
import data from "./beats.json";
import type {Beats} from "./contracts";

export const beats = data as unknown as Beats;
export const FPS = beats.fps;

/** seconds -> frame */
export const f = (seconds: number) => Math.round(seconds * FPS);
/** frame of beat `i` (clamped) */
export const beat = (i: number) => f(beats.beats[Math.max(0, Math.min(beats.beats.length - 1, i))]);
/** frame of downbeat `i` (clamped) */
export const bar = (i: number) => f(beats.downbeats[Math.max(0, Math.min(beats.downbeats.length - 1, i))]);
export const dropFrame = f(beats.drop);
/** index of the first beat at or after `seconds` */
export const beatIndexAt = (seconds: number) => {
  const i = beats.beats.findIndex((b) => b >= seconds - 1e-3);
  return i < 0 ? beats.beats.length - 1 : i;
};
/** a section's [start frame, end frame) */
export const section = (name: Beats["sections"][number]["name"]) => {
  const s = beats.sections.find((x) => x.name === name)!;
  return {from: f(s.start), to: f(s.end), frames: f(s.end) - f(s.start)};
};
/** frames of the beats inside [fromFrame, toFrame), relative to fromFrame: what ShakeOnBeat's `hits` takes */
export const hitsBetween = (fromFrame: number, toFrame: number, every = 1) =>
  beats.beats.map(f).filter((x) => x >= fromFrame && x < toFrame).filter((_, i) => i % every === 0).map((x) => x - fromFrame);
/** same, for onsets (finer) */
export const onsetsBetween = (fromFrame: number, toFrame: number) =>
  beats.onsets.map(f).filter((x) => x >= fromFrame && x < toFrame).map((x) => x - fromFrame);
/** 0..1 loudness at an absolute frame */
export const energyAt = (frame: number) => beats.energy[Math.max(0, Math.min(beats.energy.length - 1, frame))] ?? 0;
