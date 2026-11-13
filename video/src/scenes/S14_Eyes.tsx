// S14, beats 78-80: the dark before the last hit. Two amber eyes open in the black, the cheetah's, and
// burn brighter; its shape shows for a breath; three black frames; then beat 80.
import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame} from "remotion";
import {PixelCheetah} from "../components";
import {COLORS, SIZE} from "../contracts";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(78, 80);
const HEIGHT = 1150;
const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

export const S14_Eyes: React.FC = () => {
  const frame = useCurrentFrame();
  if (frame >= T.frames - 3) return <AbsoluteFill style={{backgroundColor: COLORS.black}} />;
  const glow = interpolate(frame, [0, T.at(79), T.frames - 4], [0.25, 0.7, 1], clamp);
  const body = interpolate(frame, [T.at(79), T.at(79) + 3, T.frames - 4], [0, 0.22, 0.08], clamp);
  const scale = interpolate(frame, [0, T.frames], [1, 1.06], clamp);
  const width = (HEIGHT / 20) * 19;
  return (
    <AbsoluteFill style={{backgroundColor: COLORS.black}}>
      <div style={{position: "absolute", left: (SIZE.width - width) / 2, top: (SIZE.height - HEIGHT) / 2 - 60,
                   transform: `scale(${scale.toFixed(4)})`, transformOrigin: "50% 30%"}}>
        <PixelCheetah height={HEIGHT} eyesOnly={body === 0} body={body} glow={glow} blink={frame < 2} />
      </div>
    </AbsoluteFill>
  );
};
