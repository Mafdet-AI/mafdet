import React from "react";
import {interpolate, useCurrentFrame} from "remotion";
import {COLORS, SAFE, SIZE, type CodeRevealProps} from "../contracts";
import {MONO} from "../fonts";

const FRAME = "rgba(245, 245, 245, 0.35)";
const INK = {white: COLORS.white, accent: COLORS.accent, dim: COLORS.dim} as const;

export const CodeReveal: React.FC<CodeRevealProps> = ({lines, at, frames, title, fontSize = 34, style}) => {
  const frame = useCurrentFrame();
  const total = lines.reduce((n, l) => n + l.text.length, 0);
  const progress = interpolate(frame, [at, at + Math.max(1, frames)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const typed = Math.round(progress * total);
  const done = typed >= total;

  // The line the cursor sits on: the one holding the last typed character.
  let cursorLine = 0;
  let cursorCol = 0;
  let seen = 0;
  for (let i = 0; i < lines.length; i++) {
    const len = lines[i].text.length;
    if (typed > seen || i === 0) {
      cursorLine = i;
      cursorCol = Math.min(len, typed - seen);
    }
    seen += len;
  }

  const blinkOn = !done || Math.floor((frame - (at + frames)) / 8) % 2 === 0;
  const lineHeight = Math.round(fontSize * 1.55);
  const pad = Math.round(fontSize * 1.05);
  const tab = Math.round(fontSize * 1.35);
  const cursorH = Math.round(fontSize * 1.18);

  return (
    <div
      style={{
        position: "absolute",
        left: SAFE.side,
        top: SAFE.top,
        width: SIZE.width - 2 * SAFE.side,
        boxSizing: "border-box",
        fontFamily: MONO,
        ...style,
      }}
    >
      {title ? (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: Math.round(fontSize * 0.4),
            height: tab,
            padding: `0 ${Math.round(fontSize * 0.55)}px`,
            boxSizing: "border-box",
            border: `1px solid ${FRAME}`,
            borderBottom: "none",
            verticalAlign: "bottom",
            color: COLORS.white,
            fontSize: Math.round(fontSize * 0.62),
            fontWeight: 800,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            whiteSpace: "pre",
            lineHeight: 1,
          }}
        >
          <span style={{width: Math.round(fontSize * 0.3), height: Math.round(fontSize * 0.3), background: COLORS.accent}} />
          {title}
        </div>
      ) : null}
      <div
        style={{
          border: `1px solid ${FRAME}`,
          backgroundColor: "rgba(5, 5, 5, 0.82)",
          padding: pad,
          boxSizing: "border-box",
          overflow: "hidden",
          fontSize,
          fontWeight: 500,
          lineHeight: `${lineHeight}px`,
          whiteSpace: "pre",
        }}
      >
        {lines.map((line, i) => {
          const start = lines.slice(0, i).reduce((n, l) => n + l.text.length, 0);
          const shown = Math.max(0, Math.min(line.text.length, typed - start));
          const isCursor = i === cursorLine;
          const col = isCursor ? cursorCol : shown;
          return (
            <div key={i} style={{height: lineHeight, color: INK[line.color ?? "white"]}}>
              <span>{line.text.slice(0, col)}</span>
              {isCursor ? (
                <span style={{display: "inline-block", position: "relative", width: 0, height: lineHeight, verticalAlign: "top"}}>
                  <span
                    style={{
                      position: "absolute",
                      left: 2,
                      top: Math.round((lineHeight - cursorH) / 2),
                      width: Math.round(fontSize * 0.58),
                      height: cursorH,
                      background: COLORS.accent,
                      opacity: blinkOn ? 1 : 0,
                    }}
                  />
                </span>
              ) : null}
              <span style={{opacity: 0}}>{line.text.slice(col) || " "}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
