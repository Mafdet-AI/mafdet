// The real `mafdet agent` window at work: screenshots recorded by tools/capture_tui.py, played back on the
// beat grid, with a camera that frames the part that matters.
import React from "react";
import {Easing, Img, interpolate, staticFile, useCurrentFrame} from "remotion";
import {COLORS, SIZE} from "../contracts";
import manifest from "../tui.json";

type Frame = {file: string; t: number; kitten: number};
type Clip = {frames: Frame[]; marks: Record<string, number>; duration: number};
const CLIPS = (manifest as unknown as {clips: Record<string, Clip>}).clips;

/** The screenshots' size in px (the window, its title bar included). */
export const TUI_IMAGE = {width: 1238, height: 1466} as const;

/** Seconds into a clip at which a noted moment happened (capture_tui.py's marks: "enter", "read:end"...). */
export const tuiMark = (clip: string, name: string): number => {
  const t = CLIPS[clip]?.marks[name];
  if (t === undefined) throw new Error(`no mark ${name} in clip ${clip}`);
  return t;
};
/** Seconds into the banner clip at which the cheetah's loop reaches frame `kitten` (the paw taps from 30). */
export const tuiKitten = (kitten: number): number => CLIPS.banner.frames.find((f) => f.kitten >= kitten)?.t ?? 0;
export const tuiDuration = (clip: string) => CLIPS[clip].duration;

export type TuiKey = {frame: number; t: number};
/** Where the camera looks: the point (x, y) of the screenshot, in its px, put at the screen point `screen`
 * (default the middle of the stage), magnified `scale` times over the fitted size. */
export type TuiShot = {frame: number; x: number; y: number; scale: number};

export type TuiClipProps = {
  clip: string;
  /** scene frame -> seconds of the clip, linear between keys; before the first, held; after the last,
   * the clip plays on in real time */
  keys: TuiKey[];
  /** camera keyframes: it moves in 4 frames (strong ease out) when a new one is reached */
  shots: TuiShot[];
  /** width of the whole window at scale 1, px */
  width?: number;
  /** screen point the camera's point lands on */
  screen?: {x: number; y: number};
  /** frames the camera takes to move */
  move?: number;
  /** a thin amber glow around the window */
  glow?: number;
};

const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

const timeAt = (frame: number, keys: TuiKey[]): number => {
  if (frame <= keys[0].frame) return keys[0].t;
  for (let i = 1; i < keys.length; i++) {
    if (frame <= keys[i].frame) {
      return interpolate(frame, [keys[i - 1].frame, keys[i].frame], [keys[i - 1].t, keys[i].t]);
    }
  }
  const last = keys[keys.length - 1];
  return last.t + (frame - last.frame) / 30;
};

const pick = (frames: Frame[], t: number): Frame => {
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].t <= t) lo = mid;
    else hi = mid - 1;
  }
  return frames[lo];
};

export const TuiClip: React.FC<TuiClipProps> = ({clip, keys, shots, width = 940, screen, move = 4, glow = 0.5}) => {
  const frame = useCurrentFrame();
  const shown = pick(CLIPS[clip].frames, timeAt(frame, keys));

  // the camera: from the previous shot to the current one over `move` frames
  let i = 0;
  while (i + 1 < shots.length && frame >= shots[i + 1].frame) i++;
  const cur = shots[i];
  const prev = shots[Math.max(0, i - 1)];
  const p = i === 0 ? 1 : interpolate(frame, [cur.frame, cur.frame + move], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const x = prev.x + (cur.x - prev.x) * p;
  const y = prev.y + (cur.y - prev.y) * p;
  const s = (prev.scale + (cur.scale - prev.scale) * p) * (width / TUI_IMAGE.width);
  const at = screen ?? {x: SIZE.width / 2, y: SIZE.height / 2};

  return (
    <div style={{position: "absolute", inset: 0, overflow: "hidden"}}>
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: TUI_IMAGE.width,
          height: TUI_IMAGE.height,
          transformOrigin: "0 0",
          transform: `translate(${(at.x - x * s).toFixed(2)}px, ${(at.y - y * s).toFixed(2)}px) scale(${s.toFixed(4)})`,
          filter: glow > 0 ? `drop-shadow(0 0 ${(28 * glow).toFixed(0)}px ${COLORS.accent}66)` : undefined,
        }}
      >
        <Img src={staticFile(shown.file)} style={{width: TUI_IMAGE.width, height: TUI_IMAGE.height, display: "block"}} />
      </div>
    </div>
  );
};
