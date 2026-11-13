import React from "react";
import {interpolate, useCurrentFrame} from "remotion";
import {COLORS, SAFE, SIZE, type PunchTextProps} from "../contracts";
import {DISPLAY} from "../fonts";

const RED = "#F0603A";   // MAFDET's alarm
const CYAN = "#FFC46B";  // MAFDET's hot amber

/** Rough advance width of Anton in caps, in em. Slightly generous, so the text never touches `maxWidth`. */
const widthEm = (text: string): number => {
  let w = 0;
  for (const ch of text) {
    if (ch === " ") w += 0.2;
    else if ("I1.,:;!'|".includes(ch)) w += 0.26;
    else if ("MW".includes(ch)) w += 0.64;
    else if ("-_/+=".includes(ch)) w += 0.36;
    else w += 0.48;
  }
  return Math.max(w, 0.3);
};

export const PunchText: React.FC<PunchTextProps> = ({
  text,
  at,
  size = 220,
  color = COLORS.white,
  maxWidth = SIZE.width - 2 * SAFE.side,
  split = 14,
  style,
}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  if (t < 0) return null;

  const label = text.toUpperCase();
  const fontSize = Math.min(size, maxWidth / widthEm(label));

  // 1.6x at the hit, on the mark by the third frame (2% under, the impact), settled two frames later.
  const scale = interpolate(t, [0, 1, 2, 3, 5], [1.6, 1.16, 1.03, 0.98, 1], {extrapolateRight: "clamp"});
  const blur = interpolate(t, [0, 1, 2, 3], [18, 7, 2, 0], {extrapolateRight: "clamp"});
  const opacity = interpolate(t, [0, 1], [0.86, 1], {extrapolateRight: "clamp"});
  const k = interpolate(t, [0, 6], [1, 0], {extrapolateRight: "clamp"});
  const offset = split * k * k * (t % 2 === 0 ? 1 : 0.8);
  const ghosts = t < 3 ? [1, 2] : [];

  const fill = !style || (style.top === undefined && style.bottom === undefined);
  const root: React.CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    pointerEvents: "none",
    ...(fill ? {top: 0, bottom: 0} : null),
    ...style,
  };
  const type: React.CSSProperties = {
    fontFamily: DISPLAY,
    fontSize,
    lineHeight: 1,
    letterSpacing: 0,
    textTransform: "uppercase",
    whiteSpace: "pre",
    fontWeight: 400,
  };
  const copy: React.CSSProperties = {position: "absolute", left: 0, top: 0, mixBlendMode: "screen"};

  return (
    <div style={root}>
      <div style={{...type, position: "relative", isolation: "isolate", transform: `scale(${scale})`, opacity}}>
        {ghosts.map((g) => (
          <span
            key={g}
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              color,
              opacity: (0.26 / g) * (1 - t / 3),
              transform: `scale(${1 + 0.11 * g * (1 - t / 3)})`,
              filter: `blur(${blur + 6 * g}px)`,
            }}
          >
            {label}
          </span>
        ))}
        {offset > 0.2 ? (
          <>
            <span style={{...copy, color: RED, transform: `translateX(${offset}px)`, filter: `blur(${blur * 0.5}px)`}}>
              {label}
            </span>
            <span style={{...copy, color: CYAN, transform: `translateX(${-offset}px)`, filter: `blur(${blur * 0.5}px)`}}>
              {label}
            </span>
          </>
        ) : null}
        <span style={{position: "relative", color, filter: blur > 0 ? `blur(${blur}px)` : undefined}}>{label}</span>
      </div>
    </div>
  );
};
