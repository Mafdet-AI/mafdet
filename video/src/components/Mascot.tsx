// MAFDET's own marks, drawn as the agent draws them: the pixel cheetah of the window's banner
// (mafdet/agent/banner.py, pixel for pixel, same palette) and the wordmark (mafdet/identity.py, its paths).
import React from "react";
import {COLORS} from "../contracts";

const PALETTE: Record<string, string> = {
  F: "#D8B276", L: "#EFDDBA", S: "#A07A48", K: "#33251A", E: "#E0952E", N: "#7A5040", W: "#F6EEDF",
  Y: "#B8732C", H: "#D99A50", Z: "#8A5222",
};
const CHEETAH = [
  "........F...F...", "........FF.FF...", ".......FFFFFFF..", "......FFFFFFFFF.", "......FFEFFFEFF.",
  "......FFKFNFKFF.", ".......FFKWKFF..", "........FLWLF...", ".......SFLLLF...", "......SFFLLLLF..",
  ".....SFFKFLLLF..", "....SFFFFFFLLF..", "...SFKFFSFFSLF..", "...SFFFFFKFSLF..", "..SFFSFFFFFSLFF.",
  "..SFKFFFKFFSLLF.", "..SFFFFSFFFSLLF.", "..SFFFFFFFSLLLF.", ".SFKFFSFFKFFFFFF", "..SSFFFFSFFFKFFS",
];
/** (row, column) of the two eyes */
export const CHEETAH_EYES = [[4, 8], [4, 12]] as const;
const TIP = [
  [[17, 17, "F"], [17, 18, "W"], [18, 16, "F"], [18, 17, "F"], [18, 18, "F"]],
  [[16, 18, "W"], [17, 17, "F"], [17, 18, "F"], [18, 16, "F"], [18, 17, "F"]],
] as const;
const PAW = {
  up: [[13, 14, "L"], [13, 15, "L"], [14, 14, "L"], [14, 15, "W"]],
  reach: [[14, 14, "F"], [15, 15, "L"], [15, 16, "L"], [15, 17, "W"], [16, 15, "L"], [16, 16, "W"]],
} as const;
const BALL = [".YY.", "YHYZ", "YYZZ", ".ZZ."];
export const CHEETAH_SIZE = {columns: 19, rows: 20} as const;

export type PixelCheetahProps = {
  /** height in px of the 20 pixel rows; the width follows (19 columns, 20 with the ball beside) */
  height: number;
  blink?: boolean;
  tail?: 0 | 1;
  paw?: "up" | "reach" | null;
  /** 0..1: the eyes light up, a glow around each */
  glow?: number;
  /** draw only the eyes (and their glow): the cheetah in the dark */
  eyesOnly?: boolean;
  /** 0..1 opacity of the body when eyesOnly is false */
  body?: number;
  ball?: number | null;           // the ball's left column, or none
  style?: React.CSSProperties;
};

/** The cheetah of `mafdet agent`'s banner, as crisp pixels. */
export const PixelCheetah: React.FC<PixelCheetahProps> = ({
  height, blink = false, tail = 0, paw = null, glow = 0, eyesOnly = false, body = 1, ball = null, style,
}) => {
  const columns = ball === null ? CHEETAH_SIZE.columns : Math.max(CHEETAH_SIZE.columns, ball + 4);
  const px = height / CHEETAH_SIZE.rows;
  const grid: Record<string, string> = {};
  const put = (r: number, c: number, ch: string) => {
    grid[`${r},${c}`] = PALETTE[ch];
  };
  CHEETAH.forEach((row, r) => [...row].forEach((ch, c) => ch !== "." && put(r, c, ch)));
  TIP[tail].forEach(([r, c, ch]) => put(r, c, ch));
  if (paw) PAW[paw].forEach(([r, c, ch]) => put(r, c, ch));
  CHEETAH_EYES.forEach(([r, c]) => put(r, c, blink ? "S" : "E"));
  if (ball !== null) BALL.forEach((row, r) => [...row].forEach((ch, c) => ch !== "." && put(16 + r, ball + c, ch)));
  const eyes = new Set(CHEETAH_EYES.map(([r, c]) => `${r},${c}`));

  return (
    <div style={{position: "relative", width: columns * px, height, ...style}}>
      <svg
        width={columns * px}
        height={height}
        viewBox={`0 0 ${columns} ${CHEETAH_SIZE.rows}`}
        shapeRendering="crispEdges"
        style={{position: "absolute", inset: 0, overflow: "visible"}}
      >
        {Object.entries(grid).map(([key, fill]) => {
          const isEye = eyes.has(key);
          if (eyesOnly && !isEye) return null;
          const [r, c] = key.split(",").map(Number);
          return (
            <rect key={key} x={c} y={r} width={1.02} height={1.02} fill={isEye && glow > 0 && !blink ? COLORS.hot : fill}
                  opacity={isEye ? 1 : body} />
          );
        })}
      </svg>
      {glow > 0 && !blink
        ? CHEETAH_EYES.map(([r, c]) => (
            <div
              key={`${r}-${c}`}
              style={{
                position: "absolute",
                left: (c + 0.5) * px - px * 3,
                top: (r + 0.5) * px - px * 3,
                width: px * 6,
                height: px * 6,
                borderRadius: "50%",
                background: `radial-gradient(circle, ${COLORS.hot} 0%, ${COLORS.accent}AA 22%, ${COLORS.accent}00 70%)`,
                opacity: glow,
                mixBlendMode: "screen",
              }}
            />
          ))
        : null}
    </div>
  );
};

const LETTERS: [number, string][] = [
  [0, "M0 100 L0 0 L18 0 L52 54 L86 0 L104 0 L104 100 L90 100 L90 26 L58 76 L46 76 L14 26 L14 100 Z"],
  [140, "M0 100 L39 0 L55 0 L94 100 L79 100 L47 18 L15 100 Z"],
  [270, "M0 0 L74 0 L60 14 L14 14 L14 43 L62 43 L48 57 L14 57 L14 100 L0 100 Z"],
  [380, "M0 0 L64 0 L90 26 L90 74 L64 100 L0 100 Z M14 14 L58 14 L76 32 L76 68 L58 86 L14 86 Z"],
  [506, "M0 0 L74 0 L60 14 L14 14 L14 43 L62 43 L48 57 L14 57 L14 86 L60 86 L74 100 L0 100 Z"],
  [616, "M0 0 L84 0 L70 14 L49 14 L49 100 L35 100 L35 14 L14 14 Z"],
];

/** The MAFDET wordmark, from the identity's own paths; `reveal` 0..1 uncovers it left to right. */
export const Wordmark: React.FC<{width: number; color?: string; reveal?: number; style?: React.CSSProperties}> = ({
  width, color = COLORS.white, reveal = 1, style,
}) => (
  <svg width={width} height={(width * 100) / 700} viewBox="0 0 700 100"
       style={{display: "block", clipPath: `inset(0 ${((1 - reveal) * 100).toFixed(2)}% 0 0)`, ...style}}>
    {LETTERS.map(([x, d]) => (
      <path key={x} d={d} transform={`translate(${x} 0)`} fill={color} fillRule="evenodd" />
    ))}
  </svg>
);
