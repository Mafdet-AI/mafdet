import React from "react";
import {random, useCurrentFrame} from "remotion";
import type {ShakeOnBeatProps} from "../contracts";

const MAX_ROTATION = 0.4; // degrees

export const ShakeOnBeat: React.FC<ShakeOnBeatProps> = ({hits, strength = 14, zoom = 0.06, decay = 7, children}) => {
  const frame = useCurrentFrame();

  // Each recent hit contributes an exponential tail: 1 on the hit, ~3% left at `decay`, cut after that.
  let k = 0;
  for (const hit of hits) {
    const t = frame - hit;
    if (t >= 0 && t <= decay) k += Math.exp((-3.5 * t) / Math.max(1, decay));
  }
  k = Math.min(k, 1.5);

  const active = k > 0.004;
  const x = active ? (random(`shake-${frame}`) * 2 - 1) * strength * k : 0;
  const y = active ? (random(`shake-${frame}-y`) * 2 - 1) * strength * k : 0;
  const r = active ? (random(`shake-${frame}-r`) * 2 - 1) * MAX_ROTATION * Math.min(k, 1) : 0;
  const s = 1 + zoom * k;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        transformOrigin: "50% 50%",
        transform: active
          ? `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px) rotate(${r.toFixed(3)}deg) scale(${s.toFixed(4)})`
          : undefined,
      }}
    >
      {children}
    </div>
  );
};
