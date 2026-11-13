// Small helpers shared by the scenes of block B (S09-S15).
import React from "react";
import {interpolate, useCurrentFrame} from "remotion";
import {ShakeOnBeat} from "../components";
import {COLORS, SAFE, SIZE} from "../contracts";
import {MONO} from "../fonts";

export const WIDTH = SIZE.width - 2 * SAFE.side;
/** Vertical centres of the storyboard's zones, and where a caption at the lower edge of BOTTOM sits. */
export const Y = {top: 390, middle: 860, caption: 1450, caption2: 1404} as const;
const CLAMP = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

/** The size PunchText gives a text (same estimate as the component), to place things around it. */
export const fitSize = (text: string, size: number, maxWidth: number = WIDTH): number => {
  let w = 0;
  for (const ch of text.toUpperCase()) {
    if (ch === " ") w += 0.2;
    else if ("I1.,:;!'|".includes(ch)) w += 0.26;
    else if ("MW".includes(ch)) w += 0.64;
    else if ("-_/+=".includes(ch)) w += 0.36;
    else w += 0.48;
  }
  return Math.min(size, maxWidth / Math.max(w, 0.3));
};
/** `top` of a PunchText line whose centre must sit at `cy`. */
export const lineTop = (text: string, size: number, cy: number, maxWidth: number = WIDTH) =>
  Math.round(cy - fitSize(text, size, maxWidth) / 2);

/** Children only on frames [from, until). */
export const Hold: React.FC<{from?: number; until?: number; children: React.ReactNode}> = ({
  from = 0,
  until = Infinity,
  children,
}) => {
  const frame = useCurrentFrame();
  return frame >= from && frame < until ? <>{children}</> : null;
};

/** A mono caption, centred, one or more lines, in for [from, until) with a 3-frame fade. Shrinks to fit the width. */
export const Caption: React.FC<{
  text: string | string[];
  top: number;
  from?: number;
  until?: number;
  size?: number;
  color?: string;
}> = ({text, top, from = 0, until = Infinity, size = 30, color = "#B5B5B5"}) => {
  const frame = useCurrentFrame();
  if (frame < from || frame >= until) return null;
  const lines = Array.isArray(text) ? text : [text];
  const longest = Math.max(...lines.map((l) => l.length));
  const fontSize = Math.min(size, Math.floor(WIDTH / (longest * 0.62)));
  const opacity = interpolate(frame - from, [0, 3], [0.25, 1], CLAMP);
  return (
    <div
      style={{
        position: "absolute",
        left: SAFE.side,
        width: WIDTH,
        top,
        textAlign: "center",
        fontFamily: MONO,
        fontWeight: 500,
        fontSize,
        lineHeight: `${Math.round(fontSize * 1.5)}px`,
        letterSpacing: "0.02em",
        color,
        whiteSpace: "pre",
        opacity,
      }}
    >
      {lines.map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  );
};

/** A punch-in: scales its children from 1 to `scale` between two frames, around (centre, cy). */
export const Zoom: React.FC<{from: number; to: number; scale: number; cy: number; children: React.ReactNode}> = ({
  from,
  to,
  scale,
  cy,
  children,
}) => {
  const frame = useCurrentFrame();
  const s = interpolate(frame, [from, to], [1, scale], CLAMP);
  return (
    <div style={{position: "absolute", inset: 0, transformOrigin: `50% ${cy}px`, transform: `scale(${s.toFixed(4)})`}}>
      {children}
    </div>
  );
};

/** ShakeOnBeat with a strength per hit (k = 1 the default shake, 2 the hardest): one wrapper per strength. */
export const Shake: React.FC<{hits: {at: number; k?: number}[]; children: React.ReactNode}> = ({hits, children}) => {
  const levels = [...new Set(hits.map((h) => h.k ?? 1))];
  return (
    <>
      {levels.reduce<React.ReactNode>(
        (inner, k) => (
          <ShakeOnBeat hits={hits.filter((h) => (h.k ?? 1) === k).map((h) => h.at)} strength={14 * k} zoom={0.06 * k}>
            {inner}
          </ShakeOnBeat>
        ),
        children,
      )}
    </>
  );
};

/** The geometry CodeReveal gives its box at a font size (to place things on it). */
export const codeBox = (fontSize: number, lines: number) => {
  const tab = Math.round(fontSize * 1.35);
  const pad = Math.round(fontSize * 1.05);
  const lineHeight = Math.round(fontSize * 1.55);
  const boxHeight = 2 + 2 * pad + lines * lineHeight;
  return {tab, pad, lineHeight, boxHeight, height: tab + boxHeight};
};
