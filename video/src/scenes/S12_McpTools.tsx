// S12, beats 64-72: 207 tools, 16 on at the start, the model turns on the rest itself.
import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame} from "remotion";
import {CodeReveal, GlitchTransition, PunchText} from "../components";
import {COLORS, SAFE} from "../contracts";
import {MONO} from "../fonts";
import {Caption, Hold, Shake, WIDTH, Y, Zoom, codeBox, lineTop} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(64, 72);
const CODE_TOP = 760;
const FONT_1 = 44;
const FONT_2 = 36;
const TYPE_2 = 12;
const NAME = "{name}";
const REST = ": on, call it from the next step";
const ZOOM = 1.06;

/** One phrase of TOP, on its beat, gone on the next. */
const Top: React.FC<{text: string; size: number; at: number; until: number; color?: string; maxWidth?: number}> = ({
  text,
  size,
  at,
  until,
  color,
  maxWidth,
}) => (
  <Hold from={at} until={until}>
    <PunchText text={text} at={at} size={size} color={color} maxWidth={maxWidth} style={{top: lineTop(text, size, Y.top, maxWidth)}} />
  </Hold>
);

export const S12_McpTools: React.FC = () => {
  const frame = useCurrentFrame();
  const b = (n: number) => T.at(n);
  const box = codeBox(FONT_2, 1);
  // `{name}` in accent: CodeReveal colours whole lines, so the line leaves its place blank and this types it.
  const typed = Math.round(
    interpolate(frame, [b(68), b(68) + TYPE_2], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"}) *
      (NAME.length + REST.length),
  );
  const pulse = frame >= b(71) && frame < b(71) + 4;

  return (
    <AbsoluteFill>
      <Shake hits={[{at: b(64)}, {at: b(66)}, {at: b(67)}, {at: b(68)}, {at: b(69)}, {at: b(70)}]}>
        <GlitchTransition at={b(68)} frames={4} seed={68}>
          <AbsoluteFill>
            <Zoom from={b(65)} to={b(66)} scale={ZOOM} cy={Y.top}>
              <Top text="207 TOOLS" size={300} at={b(64)} until={b(66)} maxWidth={WIDTH / ZOOM} />
            </Zoom>
            <Top text="16 START ON" size={280} at={b(66)} until={b(67)} color={COLORS.accent} />
            <Top text="1,696 TOKENS" size={260} at={b(67)} until={b(68)} />
            <Top text="FIND_TOOLS" size={280} at={b(68)} until={b(69)} color={COLORS.accent} />
            <Top text="FINDS THE REST" size={240} at={b(69)} until={b(70)} />
            <Top text="TURNS THEM ON" size={240} at={b(70)} until={T.frames} />

            <Hold until={b(68)}>
              <CodeReveal
                title="terminal"
                lines={[{text: "mafdet agent --mcp rea"}, {text: "    --profile native-static"}]}
                at={b(64)}
                frames={14}
                fontSize={FONT_1}
                style={{top: CODE_TOP}}
              />
            </Hold>
            <Hold from={b(68)}>
              <CodeReveal
                title="mafdet/agent/mcp.py"
                lines={[{text: " ".repeat(NAME.length) + REST}]}
                at={b(68)}
                frames={TYPE_2}
                fontSize={FONT_2}
                style={{top: CODE_TOP}}
              />
              <div
                style={{
                  position: "absolute",
                  left: SAFE.side + 1 + box.pad,
                  top: CODE_TOP + box.tab + 1 + box.pad,
                  fontFamily: MONO,
                  fontWeight: 500,
                  fontSize: FONT_2,
                  lineHeight: `${box.lineHeight}px`,
                  whiteSpace: "pre",
                  color: COLORS.accent,
                }}
              >
                {NAME.slice(0, typed)}
              </div>
              {pulse ? (
                <div
                  style={{
                    position: "absolute",
                    left: SAFE.side,
                    top: CODE_TOP + box.tab,
                    width: WIDTH,
                    height: box.boxHeight,
                    boxSizing: "border-box",
                    border: `3px solid ${COLORS.accent}`,
                  }}
                />
              ) : null}
            </Hold>

            <Caption text="the rea MCP server · 3.4 MB of schemas" top={Y.caption} color={COLORS.white} until={b(66)} />
            <Caption text="profile native-static: 16 tools + find_tools" top={Y.caption} color={COLORS.white} from={b(66)} until={b(68)} />
            <Caption text="the model finds and turns on the rest itself" top={Y.caption} color={COLORS.white} from={b(68)} />
          </AbsoluteFill>
        </GlitchTransition>
      </Shake>
    </AbsoluteFill>
  );
};
