import React from "react";
import {Easing, interpolate, useCurrentFrame} from "remotion";
import {COLORS, SAFE, SIZE, type PunchTextProps} from "../contracts";
import {DISPLAY} from "../fonts";
import {motionOf, type Motion} from "./motion";

const RED = "#F0603A";   // MAFDET's alarm
const CYAN = "#FFC46B";  // MAFDET's hot amber
const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;
const expo = {...clamp, easing: Easing.out(Easing.exp)};
const cubic = {...clamp, easing: Easing.out(Easing.cubic)};

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

/** A headline. How it arrives and keeps moving depends on its role (motion.ts), or on `mode`. */
export const PunchText: React.FC<PunchTextProps & {mode?: Motion}> = ({
  text,
  at,
  size = 220,
  color = COLORS.white,
  maxWidth = SIZE.width - 2 * SAFE.side,
  split = 14,
  style,
  mode,
}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  if (t < 0) return null;

  const label = text.toUpperCase();
  const motion = mode ?? motionOf(label);
  const spread = motion === "track" ? 0.08 * label.length : 0;     // room for the letters to spread again
  // fitted with room for the slow push (5%) and the scene camera's (6%): a headline never touches the edge
  const fontSize = Math.min(size, maxWidth / 1.12 / (widthEm(label) + spread));

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
  // every headline keeps moving after it lands: a slow push
  const push = interpolate(t, [6, 60], [1, 1.05], clamp);

  if (motion === "slam") {
    // 1.6x at the hit, on the mark by the third frame (2% under, the impact), settled two frames later
    const scale = interpolate(t, [0, 1, 2, 3, 5], [1.6, 1.16, 1.03, 0.98, 1], {extrapolateRight: "clamp"}) * push;
    const blur = interpolate(t, [0, 1, 2, 3], [18, 7, 2, 0], {extrapolateRight: "clamp"});
    const opacity = interpolate(t, [0, 1], [0.86, 1], {extrapolateRight: "clamp"});
    const k = interpolate(t, [0, 6], [1, 0], {extrapolateRight: "clamp"});
    const offset = split * k * k * (t % 2 === 0 ? 1 : 0.8);
    const ghosts = t < 3 ? [1, 2] : [];
    const copy: React.CSSProperties = {position: "absolute", left: 0, top: 0, mixBlendMode: "screen"};
    return (
      <div style={root}>
        <div style={{...type, position: "relative", isolation: "isolate", transform: `scale(${scale})`, opacity}}>
          {ghosts.map((g) => (
            <span key={g} style={{position: "absolute", left: 0, top: 0, color, opacity: (0.26 / g) * (1 - t / 3),
                                  transform: `scale(${1 + 0.11 * g * (1 - t / 3)})`, filter: `blur(${blur + 6 * g}px)`}}>
              {label}
            </span>
          ))}
          {offset > 0.2 ? (
            <>
              <span style={{...copy, color: RED, transform: `translateX(${offset}px)`, filter: `blur(${blur * 0.5}px)`}}>{label}</span>
              <span style={{...copy, color: CYAN, transform: `translateX(${-offset}px)`, filter: `blur(${blur * 0.5}px)`}}>{label}</span>
            </>
          ) : null}
          <span style={{position: "relative", color, filter: blur > 0 ? `blur(${blur}px)` : undefined}}>{label}</span>
        </div>
      </div>
    );
  }

  if (motion === "rise") {
    // up from behind a mask, a slight shear that straightens, then a slow drift upward
    const y = interpolate(t, [0, 7], [108, 0], expo);
    const skew = interpolate(t, [0, 7], [-7, 0], expo);
    const drift = -interpolate(t, [7, 60], [0, fontSize * 0.06], clamp);
    return (
      <div style={root}>
        <div style={{overflow: "hidden", padding: "0 0.06em", transform: `translateY(${drift}px) scale(${push})`}}>
          <div style={{...type, color, transform: `translateY(${y}%) skewY(${skew}deg)`}}>{label}</div>
        </div>
      </div>
    );
  }

  if (motion === "slideL" || motion === "slideR") {
    // whipped in from the side, two trails behind it, still moving the way it came
    const from = motion === "slideL" ? -1 : 1;          // the side it comes from: -1 left, 1 right
    const x = from * interpolate(t, [0, 6], [SIZE.width * 0.7, 0], expo) - from * interpolate(t, [6, 60], [0, 40], clamp);
    const speed = interpolate(t, [0, 6], [1, 0], cubic);
    return (
      <div style={root}>
        <div style={{...type, position: "relative", transform: `translateX(${x}px) scale(${push})`}}>
          {[3, 2, 1].map((g) =>
            speed > 0.05 ? (
              <span key={g} style={{position: "absolute", left: 0, top: 0, color, opacity: 0.22 * speed / g,
                                    transform: `translateX(${from * g * 60 * speed}px)`}}>
                {label}
              </span>
            ) : null,
          )}
          <span style={{position: "relative", color}}>{label}</span>
        </div>
      </div>
    );
  }

  if (motion === "stagger") {
    // letter by letter, each falling into place one frame after the one before
    return (
      <div style={root}>
        <div style={{...type, display: "flex", transform: `scale(${push})`}}>
          {[...label].map((ch, i) => {
            const lt = t - i;
            const y = interpolate(lt, [0, 5], [-55, 0], expo);
            const o = interpolate(lt, [0, 2], [0, 1], clamp);
            const s = interpolate(lt, [0, 5], [1.35, 1], expo);
            return (
              <span key={i} style={{color, opacity: o, transform: `translateY(${y}%) scale(${s})`, display: "inline-block",
                                    whiteSpace: "pre"}}>
                {ch}
              </span>
            );
          })}
        </div>
      </div>
    );
  }

  if (motion === "type") {
    // typed on, about two letters a frame, an amber block cursor after the last
    const shown = Math.min(label.length, Math.floor(t * 2) + 1);
    const done = shown >= label.length;
    const cursorOn = !done || Math.floor((t - label.length / 2) / 6) % 2 === 0;
    return (
      <div style={root}>
        <div style={{...type, position: "relative", transform: `scale(${push})`}}>
          <span style={{color, visibility: "hidden"}}>{label}</span>
          <span style={{position: "absolute", left: 0, top: 0, color}}>
            {label.slice(0, shown)}
            {cursorOn ? <span style={{display: "inline-block", width: "0.42em", height: "0.78em", marginLeft: "0.05em",
                                      background: COLORS.accent, verticalAlign: "-0.02em"}} /> : null}
          </span>
        </div>
      </div>
    );
  }

  // track: letters pulled together from wide spacing, then slowly spreading again
  const spacing = interpolate(t, [0, 10], [0.55, 0.01], expo) + interpolate(t, [10, 70], [0, 0.07], clamp);
  const o = interpolate(t, [0, 4], [0, 1], clamp);
  const blur = interpolate(t, [0, 6], [8, 0], cubic);
  return (
    <div style={root}>
      <div style={{...type, color, letterSpacing: `${spacing}em`, marginRight: `-${spacing}em`, opacity: o,
                   filter: blur > 0.2 ? `blur(${blur}px)` : undefined}}>
        {label}
      </div>
    </div>
  );
};
