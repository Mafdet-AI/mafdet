// Helpers of block A (S01-S08): zones, stacked punch lines, captions, weighted shakes, slow zooms.
import React from "react";
import {interpolate, useCurrentFrame} from "remotion";
import {PunchText, ShakeOnBeat} from "../components";
import {motionOf} from "../components/motion";
import {COLORS, SAFE, SIZE} from "../contracts";
import {MONO} from "../fonts";

const SAFE_H = SIZE.height - SAFE.top - SAFE.bottom;
const TOP_H = 340;
const BOTTOM_H = 340;
/** The three zones of the storyboard inside the safe area, as {top, height} in px. */
export const ZONE = {
  top: {top: SAFE.top, height: TOP_H},
  middle: {top: SAFE.top + TOP_H, height: SAFE_H - TOP_H - BOTTOM_H},
  bottom: {top: SIZE.height - SAFE.bottom - BOTTOM_H, height: BOTTOM_H},
} as const;
export const SAFE_W = SIZE.width - 2 * SAFE.side;
/** Top of a one-line caption sitting on the lower edge of BOTTOM. */
export const CAPTION_TOP = SIZE.height - SAFE.bottom - 62;

const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

export type Hit = {at: number; k?: number};
/** ShakeOnBeat with a weight per hit (x0.5, x1.5, x2): one nested wrapper per weight. */
export const Shake: React.FC<{hits: Hit[]; children: React.ReactNode}> = ({hits, children}) => {
  const weights = [...new Set(hits.map((h) => h.k ?? 1))];
  return weights.reduce<React.ReactElement>(
    (inner, k) => (
      <ShakeOnBeat hits={hits.filter((h) => (h.k ?? 1) === k).map((h) => h.at)} strength={k > 1 ? 14 * k : 6 * k}
                   zoom={k > 1 ? 0.05 * k : 0.025 * k}>
        {inner}
      </ShakeOnBeat>
    ),
    <>{children}</>,
  );
};

/** A linear zoom of everything inside, from 1 at `from` to `to` at `until`, held after. */
export const Zoom: React.FC<{from: number; until: number; to: number; originY: number; children: React.ReactNode}> = ({
  from,
  until,
  to,
  originY,
  children,
}) => {
  const frame = useCurrentFrame();
  const s = interpolate(frame, [from, until], [1, to], clamp);
  return (
    <div style={{position: "absolute", inset: 0, transformOrigin: `50% ${originY}px`, transform: `scale(${s.toFixed(4)})`}}>
      {children}
    </div>
  );
};

/** Main text: one PunchText per line of `text` ("\n" breaks), stacked and centred in a zone, on screen for
 * at <= frame < until. `zoom` is a punch-in to that scale by `until`. `after` sits right of the last line. */
export const Punch: React.FC<{
  text: string;
  at: number;
  until?: number;
  size?: number;
  color?: string;
  split?: number;
  zone?: {top: number; height: number};
  pitch?: number;
  zoom?: number;
  maxWidth?: number;
  after?: React.ReactNode;
  /** when the PunchText itself hits, if not `at` (earlier = already settled when it shows) */
  hitAt?: number;
}> = ({text, at, until = Infinity, size = 220, color, split, zone = ZONE.middle, pitch, zoom, maxWidth, after, hitAt}) => {
  const frame = useCurrentFrame();
  if (frame < at || frame >= until) return null;
  const lines = text.split("\n");
  const step = pitch ?? Math.round(size * 0.94);
  const total = (lines.length - 1) * step + size;
  const first = zone.top + (zone.height - total) / 2;
  const width = maxWidth ?? SAFE_W / (zoom ?? 1);
  const stack = lines.map((line, i) => (
    <div
      key={i}
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: first + i * step,
        height: size,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <PunchText text={line} at={(hitAt ?? at) + (motionOf(line.toUpperCase()) === "slam" ? 0 : i * 3)} size={size}
                 color={color} split={split} maxWidth={width} style={{position: "relative"}} />
      {i === lines.length - 1 ? after : null}
    </div>
  ));
  if (!zoom || !Number.isFinite(until)) return <>{stack}</>;
  return (
    <Zoom from={at} until={until} to={zoom} originY={zone.top + zone.height / 2}>
      {stack}
    </Zoom>
  );
};

/** A mono caption, centred, with a 3-frame fade in. Never upper-cased. */
export const Caption: React.FC<{text: string; top?: number; from?: number; until?: number; color?: string; size?: number}> = ({
  text,
  top = CAPTION_TOP,
  from = 0,
  until = Infinity,
  color = COLORS.soft,
  size = 30,
}) => {
  const frame = useCurrentFrame();
  if (frame < from || frame >= until) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: SAFE.side,
        width: SAFE_W,
        top,
        textAlign: "center",
        fontFamily: MONO,
        fontWeight: 500,
        fontSize: size,
        lineHeight: 1.4,
        letterSpacing: "0.02em",
        whiteSpace: "pre",
        color,
        opacity: interpolate(frame, [from - 1, from + 2], [0, 1], clamp),
      }}
    >
      {text}
    </div>
  );
};
