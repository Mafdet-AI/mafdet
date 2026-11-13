// The edit: every scene of storyboard.json in its place on the beat grid, the track under it, film on top.
//
// Movement comes from here as much as from the scenes: each scene enters with a camera move of its own
// (a zoom through, a whip from the left or the right, a drop), in turn, instead of a flash; then the camera
// keeps pushing in and panning slowly, so no frame is ever still. Over it, a warm light drifts, and a sweep of
// light crosses the frame on the first beat of every other bar of the drop. Flashes are kept for the three
// hits that carry the edit (the drop, the relaunch, the last hit), in their scenes.
import React from "react";
import {AbsoluteFill, Audio, Easing, interpolate, Sequence, staticFile, useCurrentFrame} from "remotion";
import {GrainOverlay} from "./components";
import {COLORS, SIZE} from "./contracts";
import {blockA} from "./scenes/blockA";
import {blockB} from "./scenes/blockB";
import storyboard from "./storyboard.json";
import {beat, beats, f} from "./timing";

type Entry = {id: string; file: string; component: string; fromBeat: number; toBeat: number; block: "A" | "B"};
const scenes: Record<string, React.FC> = {...blockA, ...blockB};
const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;
const expo = {...clamp, easing: Easing.out(Easing.exp)};

const ENTRIES = ["zoom", "whipL", "whipR", "drop"] as const;
/** Scenes that keep their own camera: the cover frame of the hook, and the dark close. */
const OWN_CAMERA = new Set(["S01", "S14", "S15"]);
const ENTRY_FRAMES = 7;

const SceneCamera: React.FC<{index: number; frames: number; children: React.ReactNode}> = ({index, frames, children}) => {
  const frame = useCurrentFrame();
  const kind = ENTRIES[index % ENTRIES.length];
  const p = interpolate(frame, [0, ENTRY_FRAMES], [0, 1], expo);
  const q = 1 - p;
  // the slow move that never stops: in, and a pan that alternates direction from scene to scene
  const push = interpolate(frame, [0, frames], [1, 1.06], clamp);
  const pan = (index % 2 ? 1 : -1) * interpolate(frame, [0, frames], [-16, 16], clamp);
  let x = 0;
  let y = 0;
  let s = 1;
  let r = 0;
  if (kind === "zoom") s = 1 + 0.28 * q;
  if (kind === "whipL") x = -320 * q;
  if (kind === "whipR") x = 320 * q;
  if (kind === "drop") {
    y = -220 * q;
    r = -2.5 * q;
  }
  const blur = 14 * q;
  return (
    <AbsoluteFill style={{transform: `translate(${(x + pan).toFixed(2)}px, ${y.toFixed(2)}px) scale(${(s * push).toFixed(4)}) rotate(${r.toFixed(2)}deg)`,
                          filter: blur > 0.3 ? `blur(${blur.toFixed(1)}px)` : undefined}}>
      {children}
    </AbsoluteFill>
  );
};

/** Warm light that drifts, and a sweep across the frame on every other bar of the drop. */
const Light: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  const ax = 30 + 25 * Math.sin(t * 0.35);
  const ay = 35 + 20 * Math.cos(t * 0.27);
  const bx = 70 + 22 * Math.cos(t * 0.31);
  const by = 70 + 18 * Math.sin(t * 0.23);
  const drop = beats.sections.find((x) => x.name === "drop")!;
  const sweeps = beats.downbeats.filter((d) => d >= drop.start && d < drop.end).filter((_, i) => i % 2 === 0).map(f);
  const last = sweeps.filter((s) => s <= frame).pop();
  const sp = last === undefined ? 1 : interpolate(frame - last, [0, 12], [0, 1], clamp);
  return (
    <AbsoluteFill style={{pointerEvents: "none", mixBlendMode: "screen"}}>
      <AbsoluteFill style={{background: `radial-gradient(circle at ${ax}% ${ay}%, ${COLORS.accent}26 0%, transparent 38%),
                                         radial-gradient(circle at ${bx}% ${by}%, ${COLORS.hot}14 0%, transparent 34%)`}} />
      {sp < 1 ? (
        <div style={{position: "absolute", top: -SIZE.height * 0.25, height: SIZE.height * 1.5, width: 260,
                     left: interpolate(sp, [0, 1], [-400, SIZE.width + 140]), transform: "rotate(18deg)",
                     background: `linear-gradient(90deg, transparent, ${COLORS.hot}22, transparent)`}} />
      ) : null}
    </AbsoluteFill>
  );
};

export const Edit: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Audio src={staticFile("audio/track.wav")} />
    {(storyboard as Entry[]).map((s, i) => {
      const from = beat(s.fromBeat);
      const to = s.toBeat >= beats.beats.length ? beats.durationInFrames : beat(s.toBeat);
      const Scene = scenes[s.component];
      const body = Scene ? <Scene /> : null;
      return (
        <Sequence key={s.id} from={from} durationInFrames={to - from} name={s.file}>
          {OWN_CAMERA.has(s.id) ? body : <SceneCamera index={i} frames={to - from}>{body}</SceneCamera>}
        </Sequence>
      );
    })}
    <Light />
    <GrainOverlay />
  </AbsoluteFill>
);
