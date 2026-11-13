// S10, beats 52-60: the three approval modes, then the real line of code under the rules that never change.
import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";
import {CodeReveal, FlashCut, GlitchTransition, PunchText} from "../components";
import {COLORS, SAFE} from "../contracts";
import {MONO} from "../fonts";
import {Caption, Hold, Shake, WIDTH, Y, fitSize, lineTop} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(52, 60);
const MODES = ["ask", "edits", "all"] as const;
const ROW = {top: 1200, height: 116, gap: 32};
const RULE_SIZE = 170;
const RULE_TOP = 228;

/** Three thin boxes, the current mode filled with the accent. */
const ModeRow: React.FC<{current: number}> = ({current}) => {
  const w = (WIDTH - 2 * ROW.gap) / 3;
  return (
    <>
      {MODES.map((m, i) => (
        <div
          key={m}
          style={{
            position: "absolute",
            left: SAFE.side + i * (w + ROW.gap),
            top: ROW.top,
            width: w,
            height: ROW.height,
            boxSizing: "border-box",
            border: `2px solid ${i === current ? COLORS.accent : COLORS.white}`,
            background: i === current ? COLORS.accent : "transparent",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: MONO,
            fontWeight: 800,
            fontSize: 40,
            letterSpacing: "0.02em",
            color: i === current ? COLORS.white : COLORS.dim,
          }}
        >
          {m}
        </div>
      ))}
    </>
  );
};

/** A rule of bar 14: two lines of the same size filling TOP. */
const Rule: React.FC<{lines: [string, string]; at: number; until: number; color?: string; split?: number}> = ({
  lines,
  at,
  until,
  color,
  split,
}) => {
  const size = Math.min(fitSize(lines[0], RULE_SIZE), fitSize(lines[1], RULE_SIZE));
  return (
    <Hold from={at} until={until}>
      <PunchText text={lines[0]} at={at} size={size} color={color} split={split} style={{top: RULE_TOP}} />
      <PunchText text={lines[1]} at={at} size={size} color={color} split={split} style={{top: RULE_TOP + Math.round(size * 1.02)}} />
    </Hold>
  );
};

export const S10_Approvals: React.FC = () => {
  const frame = useCurrentFrame();
  const b = (n: number) => T.at(n);
  const cycle = Math.min(2, Math.floor(((frame - b(55)) / (b(56) - b(55))) * 3));
  const current = frame < b(53) ? 0 : frame < b(54) ? 1 : frame < b(55) ? 2 : cycle;

  return (
    <AbsoluteFill>
      <Shake
        hits={[{at: b(52)}, {at: b(53)}, {at: b(54), k: 1.5}, {at: b(55)}, {at: b(56)}, {at: b(58)}, {at: b(59)}]}
      >
        <GlitchTransition at={T.frames - 4} frames={4} seed={60}>
          <AbsoluteFill>
            {/* bar 13: one mode per beat */}
            <Hold until={b(56)}>
              <Hold until={b(53)}>
                <PunchText text="ASK" at={b(52)} size={420} style={{top: lineTop("ASK", 420, Y.middle)}} />
              </Hold>
              <Hold from={b(53)} until={b(54)}>
                <PunchText text="EDITS" at={b(53)} size={420} style={{top: lineTop("EDITS", 420, Y.middle)}} />
              </Hold>
              <Hold from={b(54)} until={b(55)}>
                <PunchText text="ALL" at={b(54)} size={420} color={COLORS.accent} style={{top: lineTop("ALL", 420, Y.middle)}} />
              </Hold>
              <Hold from={b(55)}>
                <PunchText text="SHIFT+TAB" at={b(55)} size={260} style={{top: lineTop("SHIFT+TAB", 260, Y.middle)}} />
              </Hold>
              <ModeRow current={current} />
              <Caption text="cycles ask -> edits -> all" top={ROW.top + ROW.height + 40} from={b(55)} size={32} />
            </Hold>

            {/* bar 14: the line of code, still, under four rules */}
            <Hold from={b(56)}>
              <Rule lines={["WRITES", "ASK FIRST"]} at={b(56)} until={b(57)} />
              <Rule lines={["SHELL", "ASKS FIRST"]} at={b(57)} until={b(58)} />
              <Rule lines={["OUTSIDE", "ALWAYS ASKS"]} at={b(58)} until={b(59)} />
              <Rule lines={["INSTALLS", "ALWAYS ASK"]} at={b(59)} until={T.frames} color={COLORS.accent} split={20} />
              <CodeReveal
                title="mafdet/agent/permissions.py"
                lines={[{text: 'MODES = ("ask", "edits", "all")'}]}
                at={b(56)}
                frames={10}
                fontSize={34}
                style={{top: 770}}
              />
              <Caption text="ask mode: writes, the shell, MCP tools not read-only" top={Y.caption} from={b(56)} until={b(58)} />
              <Caption text="anything outside the working folder" top={Y.caption} from={b(58)} until={b(59)} />
              <Caption text="in every mode" top={Y.caption} from={b(59)} />
            </Hold>
          </AbsoluteFill>
        </GlitchTransition>
      </Shake>
      <FlashCut at={b(52)} frames={2} />
    </AbsoluteFill>
  );
};
