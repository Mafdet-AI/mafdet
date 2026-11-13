import React from "react";
import {Easing, interpolate, useCurrentFrame} from "remotion";
import {COLORS, SAFE, SIZE, type FlowDiagramProps} from "../contracts";
import {DISPLAY, MONO} from "../fonts";

const NODE_H = 130;
const GAP = 58;
const STROKE = 2;
const GUTTER = 84; // room on the right for the loop-back edge
const RAIL_X = 64; // where the connectors run, from the left edge of the nodes
const IN = 4; // frames a node takes to land, and its connector to draw
const LOOP = 6; // frames the loop-back edge takes to draw

const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;

export const FlowDiagram: React.FC<FlowDiagramProps> = ({nodes, appear, loopBack, style}) => {
  const frame = useCurrentFrame();
  const n = nodes.length;
  const width = typeof style?.width === "number" ? style.width : SIZE.width - 2 * SAFE.side;
  const gap =
    typeof style?.height === "number" && n > 1 ? Math.max(20, (style.height - n * NODE_H) / (n - 1)) : GAP;
  const height = n * NODE_H + (n - 1) * gap;
  const nodeW = width - (loopBack ? GUTTER : 0);
  const top = (i: number) => i * (NODE_H + gap);

  // The node that holds the accent: the last one that landed, or the loop's target once the loop has closed.
  let active = -1;
  let activeSince = -Infinity;
  for (let i = 0; i < n; i++) {
    const a = appear[i] ?? Infinity;
    if (a <= frame && a >= activeSince) {
      active = i;
      activeSince = a;
    }
  }
  if (loopBack && frame >= loopBack.at + LOOP - 1 && loopBack.at + LOOP - 1 >= activeSince) {
    active = loopBack.to;
    activeSince = loopBack.at + LOOP - 1;
  }

  // The loop-back edge: out of `from` on the right, up the gutter, into `to`, arrow head on arrival.
  let loop: React.ReactNode = null;
  if (loopBack && frame >= loopBack.at) {
    const x0 = nodeW;
    const xg = nodeW + GUTTER - 16;
    const yFrom = top(loopBack.from) + NODE_H / 2;
    const yTo = top(loopBack.to) + NODE_H / 2;
    const tip = x0 + 4;
    const length = xg - x0 + Math.abs(yFrom - yTo) + (xg - tip - 16);
    const p = interpolate(frame - loopBack.at + 1, [0, LOOP], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
    loop = (
      <svg width={width} height={height} style={{position: "absolute", left: 0, top: 0, overflow: "visible"}}>
        <path
          d={`M ${x0} ${yFrom} H ${xg} V ${yTo} H ${tip + 16}`}
          fill="none"
          stroke={COLORS.accent}
          strokeWidth={STROKE}
          strokeLinecap="butt"
          strokeLinejoin="miter"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - p)}
          shapeRendering="crispEdges"
        />
        {p >= 1 ? (
          <polygon points={`${tip},${yTo} ${tip + 20},${yTo - 11} ${tip + 20},${yTo + 11}`} fill={COLORS.accent} />
        ) : null}
      </svg>
    );
  }

  return (
    <div
      style={{position: "absolute", left: SAFE.side, top: SAFE.top, ...style, width, height, boxSizing: "border-box"}}
    >
      {nodes.map((node, i) => {
        const a = appear[i] ?? Infinity;

        // connector from the previous node, drawn top to bottom over the frames before the landing
        const line = i > 0 ? interpolate(frame, [a - IN, a], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)}) : 0;
        const connector =
          line > 0 ? (
            <div
              key={`c${i}`}
              style={{
                position: "absolute",
                left: RAIL_X - STROKE / 2,
                top: top(i - 1) + NODE_H,
                width: STROKE,
                height: gap * line,
                background: COLORS.white,
              }}
            />
          ) : null;

        const t = frame - a;
        if (t < 0) return connector;

        // lands on the beat: already 4/5 of the way on frame `a`, home three frames later
        const p = interpolate(t + 1, [0, IN], [0, 1], {...clamp, easing: Easing.out(Easing.exp)});
        const isActive = i === active;
        const flash = isActive && frame === activeSince; // one white frame on the hit, then the accent
        const fill = flash ? COLORS.white : isActive ? COLORS.accent : "transparent";
        const edge = flash ? COLORS.white : isActive ? COLORS.accent : COLORS.white;
        const ink = flash ? COLORS.black : COLORS.white;
        const label = node.label.toUpperCase();
        const labelSize = Math.min(node.note ? 58 : 68, (nodeW - 150) / Math.max(1, label.length * 0.48));

        return (
          <React.Fragment key={i}>
            {connector}
            {/* two trailing ghosts while it travels */}
            {t < 2
              ? [1, 2].map((g) => (
                  <div
                    key={`g${g}`}
                    style={{
                      position: "absolute",
                      left: 0,
                      top: top(i),
                      width: nodeW,
                      height: NODE_H,
                      boxSizing: "border-box",
                      background: edge,
                      opacity: 0.16 / g / (t + 1),
                      transform: `translateX(${-60 * (1 - p) - 34 * g}px)`,
                      clipPath: `inset(0 ${(1 - p) * 100}% 0 0)`,
                    }}
                  />
                ))
              : null}
            <div
              style={{
                position: "absolute",
                left: 0,
                top: top(i),
                width: nodeW,
                height: NODE_H,
                boxSizing: "border-box",
                border: `${STROKE}px solid ${edge}`,
                background: fill,
                transform: `translateX(${-60 * (1 - p)}px)`,
                clipPath: `inset(0 ${(1 - p) * 100}% 0 0)`,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                padding: "0 110px 0 30px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  fontFamily: DISPLAY,
                  fontSize: labelSize,
                  lineHeight: 1.08,
                  color: ink,
                  textTransform: "uppercase",
                  whiteSpace: "pre",
                }}
              >
                {label}
              </div>
              {node.note ? (
                <div
                  style={{
                    fontFamily: MONO,
                    fontWeight: 500,
                    fontSize: 27,
                    lineHeight: 1.3,
                    marginTop: 2,
                    color: flash ? COLORS.black : isActive ? COLORS.white : "#B0B0B0",
                    whiteSpace: "pre",
                  }}
                >
                  {node.note}
                </div>
              ) : null}
              <div
                style={{
                  position: "absolute",
                  right: 24,
                  top: 0,
                  bottom: 0,
                  display: "flex",
                  alignItems: "center",
                  fontFamily: MONO,
                  fontWeight: 800,
                  fontSize: 26,
                  color: flash ? COLORS.black : isActive ? COLORS.white : COLORS.dim,
                }}
              >
                {String(i + 1).padStart(2, "0")}
              </div>
            </div>
          </React.Fragment>
        );
      })}
      {loop}
    </div>
  );
};
