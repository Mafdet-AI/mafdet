import React, {useId} from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";
import type {GrainOverlayProps} from "../contracts";

/** The noise is computed at half resolution and doubled: 2 px grain survives video compression, 1 px does not. */
const DOWNSCALE = 2;

export const GrainOverlay: React.FC<GrainOverlayProps> = ({opacity = 0.18}) => {
  const frame = useCurrentFrame();
  const id = `grain-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  const noise = (blend: React.CSSProperties["mixBlendMode"], alpha: number) => (
    <AbsoluteFill style={{mixBlendMode: blend, opacity: alpha}}>
      <svg
        width={`${100 / DOWNSCALE}%`}
        height={`${100 / DOWNSCALE}%`}
        style={{transform: `scale(${DOWNSCALE})`, transformOrigin: "0 0", imageRendering: "pixelated"}}
      >
        <rect width="100%" height="100%" filter={`url(#${id})`} />
      </svg>
    </AbsoluteFill>
  );

  return (
    <AbsoluteFill style={{pointerEvents: "none"}}>
      <svg width={0} height={0} style={{position: "absolute"}}>
        <defs>
          <filter id={id} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} seed={(frame * 7 + 3) % 9973} />
            {/* grey, opaque, contrast pushed around mid grey */}
            <feColorMatrix type="matrix" values="1.1 1.1 1.1 0 -1.15  1.1 1.1 1.1 0 -1.15  1.1 1.1 1.1 0 -1.15  0 0 0 0 1" />
          </filter>
        </defs>
      </svg>
      {/* overlay grains the whites and the accent, screen lifts a little of it into the blacks */}
      {noise("overlay", Math.min(1, opacity * 2.2))}
      {noise("screen", opacity * 0.45)}
      {/* scanlines */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "repeating-linear-gradient(to bottom, rgba(0,0,0,0) 0px, rgba(0,0,0,0) 3px, rgba(0,0,0,0.22) 3px, rgba(0,0,0,0.22) 4px)",
          opacity: Math.min(1, opacity * 3),
        }}
      />
      {/* vignette */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse 86% 76% at 50% 50%, rgba(0,0,0,0) 62%, rgba(0,0,0,0.3) 88%, rgba(0,0,0,0.7) 100%)",
        }}
      />
    </AbsoluteFill>
  );
};
