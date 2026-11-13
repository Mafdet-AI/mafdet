import React from "react";
import {AbsoluteFill, random, useCurrentFrame} from "remotion";
import type {GlitchTransitionProps} from "../contracts";

const GROUPS = 3;
/** Sideways travel of each group of bands at intensity 1, px: one nearly still (the eye keeps the image), two thrown. */
const TRAVEL = [10, 84, 168];

/** A clip-path made of full-width horizontal bands, [top, bottom] in percent. */
const bandsToPolygon = (bands: [number, number][]): string =>
  `polygon(${bands.map(([a, b]) => `0% ${a}%, 100% ${a}%, 100% ${b}%, 0% ${b}%`).join(", ")})`;

export const GlitchTransition: React.FC<GlitchTransitionProps> = ({at, frames = 5, seed = 0, intensity = 1, children}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  if (t < 0 || t >= frames) return <>{children}</>;

  const key = `glitch-${seed}-${t}`;
  const env = intensity * (1 - (0.65 * t) / frames);

  // Cut the frame in 7-12 bands of uneven height and deal them to the groups.
  const count = 7 + Math.floor(random(`${key}-n`) * 6);
  const cuts = Array.from({length: count - 1}, (_, i) => random(`${key}-c${i}`) * 100).sort((a, b) => a - b);
  const edges = [0, ...cuts, 100];
  const groups: [number, number][][] = Array.from({length: GROUPS}, () => []);
  for (let i = 0; i < count; i++) {
    const g = Math.floor(random(`${key}-g${i}`) * GROUPS) % GROUPS;
    groups[g].push([edges[i], edges[i + 1]]);
  }

  return (
    <AbsoluteFill>
      <svg width={0} height={0} style={{position: "absolute"}}>
        <defs>
          <filter id="glitch-red" x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
            <feColorMatrix type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" />
          </filter>
          <filter id="glitch-cyan" x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
            <feColorMatrix type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" />
          </filter>
        </defs>
      </svg>
      {groups.map((bands, g) => {
        if (bands.length === 0) return null;
        const side = random(`${key}-s${g}`) < 0.5 ? -1 : 1;
        const x = side * TRAVEL[g] * env * (0.6 + 0.4 * random(`${key}-x${g}`));
        const split = (5 + 17 * random(`${key}-p${g}`)) * env;
        return (
          <AbsoluteFill
            key={g}
            style={{clipPath: bandsToPolygon(bands), transform: `translateX(${x.toFixed(1)}px)`, isolation: "isolate"}}
          >
            <AbsoluteFill style={{filter: "url(#glitch-red)", transform: `translateX(${split.toFixed(1)}px)`}}>
              {children}
            </AbsoluteFill>
            <AbsoluteFill
              style={{filter: "url(#glitch-cyan)", transform: `translateX(${(-split).toFixed(1)}px)`, mixBlendMode: "screen"}}
            >
              {children}
            </AbsoluteFill>
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};
