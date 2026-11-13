// S02, beats 4-12: what it is, one phrase per beat, calmer than the hook.
import React from "react";
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from "remotion";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake, ZONE} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(4, 12);
const SIZE_MAIN = 220;
const SLIDE = 6;
const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

/** Beat 4: the phrase comes up from under the lower edge of MIDDLE, 6 frames, no slam. */
const SlideUp: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame + 1, [T.at(4), T.at(4) + SLIDE], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const zone = ZONE.middle;
  const bottom = zone.top + zone.height;
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        clipPath: `polygon(0px ${zone.top}px, 100% ${zone.top}px, 100% ${bottom}px, 0px ${bottom}px)`,
      }}
    >
      <div style={{position: "absolute", inset: 0, transform: `translateY(${((1 - p) * zone.height).toFixed(1)}px)`}}>{children}</div>
    </div>
  );
};

/** Beat 6: a speed ramp, in from far and fast, then a slow creep to the end of the beat. */
const Ramp: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  const t = frame - T.at(6);
  const slam = interpolate(t, [0, 1, 2, 3], [1.5, 1.22, 1.05, 1], clamp);
  const creep = interpolate(frame, [T.at(6) + 3, T.at(7)], [1, 1.05], clamp);
  const y = ZONE.middle.top + ZONE.middle.height / 2;
  return (
    <div style={{position: "absolute", inset: 0, transformOrigin: `50% ${y}px`, transform: `scale(${(slam * creep).toFixed(4)})`}}>
      {children}
    </div>
  );
};

/** Beat 7: a block cursor after the E, 4 frames on, 4 off. */
const BlockCursor: React.FC = () => {
  const frame = useCurrentFrame();
  const on = Math.floor((frame - T.at(7)) / 4) % 2 === 0;
  return (
    <div
      style={{
        width: Math.round(SIZE_MAIN * 0.4),
        height: Math.round(SIZE_MAIN * 0.76),
        marginLeft: Math.round(SIZE_MAIN * 0.07),
        background: COLORS.accent,
        opacity: on ? 1 : 0,
      }}
    />
  );
};

export const S02_WhatItIs: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Shake hits={[{at: T.at(4)}, {at: T.at(6)}, {at: T.at(8)}, {at: T.at(10)}]}>
      <SlideUp>
        <Punch text={"A LOCAL\nMODEL"} at={T.at(4)} until={T.at(5)} size={SIZE_MAIN} hitAt={T.at(4) - T.beatFrames} />
      </SlideUp>
      <Punch text={"ON YOUR\nMACHINE"} at={T.at(5)} until={T.at(6)} size={SIZE_MAIN} />
      <Ramp>
        <Punch text="THINKING" at={T.at(6)} until={T.at(7)} size={280} color={COLORS.accent} split={24} maxWidth={880} />
      </Ramp>
      <Punch text={"WHILE\nYOU TYPE"} at={T.at(7)} until={T.at(8)} size={SIZE_MAIN} after={<BlockCursor />} />
      <Punch text={"READS\nFILES"} at={T.at(8)} until={T.at(9)} size={SIZE_MAIN} />
      <Punch text={"RUNS\nCOMMANDS"} at={T.at(9)} until={T.at(10)} size={SIZE_MAIN} />
      <Punch text={"USES\nMCP TOOLS"} at={T.at(10)} until={T.at(11)} size={SIZE_MAIN} />
      <Punch text={"UNTIL\nIT'S DONE"} at={T.at(11)} until={T.frames} size={SIZE_MAIN} color={COLORS.accent} zoom={1.06} />
      <Caption text="mafdet agent · experimental" />
    </Shake>
  </AbsoluteFill>
);
