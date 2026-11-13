// The frame the real window is shown in: a band between two thin amber lines, under the words of the
// scene, over its caption. Used by the scenes that show `mafdet agent` at work (S04, S07, S08, S10).
import React from "react";
import {interpolate, useCurrentFrame} from "remotion";
import {TuiClip, type TuiKey, type TuiShot} from "../components";
import {COLORS, SIZE} from "../contracts";

export const VIEW = {top: 590, height: 800} as const;
/** What a caption under a live clip says: the window and the tools are real, the model follows a script. */
export const LIVE_NOTE = "the real window · real tools · a scripted model for the demo";

export const Viewport: React.FC<{
  clip: string;
  keys: TuiKey[];
  shots: TuiShot[];
  /** frame the band opens (its lines draw out from the middle in 4 frames) */
  at?: number;
}> = ({clip, keys, shots, at = 0}) => {
  const frame = useCurrentFrame();
  const open = interpolate(frame, [at, at + 4], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const line = (top: number) => (
    <div style={{position: "absolute", left: SIZE.width * (1 - open) / 2, width: SIZE.width * open, top, height: 2,
                 background: COLORS.accent}} />
  );
  return (
    <>
      <div style={{position: "absolute", left: 0, right: 0, top: VIEW.top, height: VIEW.height, overflow: "hidden",
                   clipPath: `inset(${((1 - open) * 50).toFixed(2)}% 0)`}}>
        <TuiClip clip={clip} keys={keys} shots={shots} screen={{x: SIZE.width / 2, y: VIEW.height / 2}} />
      </div>
      {line(VIEW.top - 2)}
      {line(VIEW.top + VIEW.height)}
    </>
  );
};
