import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";
import {COLORS, type FlashCutProps} from "../contracts";

export const FlashCut: React.FC<FlashCutProps> = ({at, frames = 2, color = COLORS.white}) => {
  const t = useCurrentFrame() - at;
  if (t < 0 || t >= frames) return null;
  // 1 on the hit, 0.5 on the next frame, then down to nothing at `frames`.
  const opacity = t === 0 ? 1 : 0.5 * (1 - (t - 1) / Math.max(1, frames - 1));
  return <AbsoluteFill style={{backgroundColor: color, opacity, pointerEvents: "none"}} />;
};
