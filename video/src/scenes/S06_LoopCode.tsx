// S06, beats 32-36: the loop's real code, held for the whole bar under a changing headline.
import React from "react";
import {AbsoluteFill} from "remotion";
import {CodeReveal, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(32, 36);
const GLITCH = 4;
const FONT = 44;
const CODE_TOP = 640;
const LINES = [
  {text: "if not reply.tool_calls:"},
  {text: "    ...", color: "dim" as const},
  {text: "    return reply.content", color: "accent" as const},
  {text: "for call in reply.tool_calls:"},
  {text: "    if self.stop.is_set():"},
  {text: "        raise Stopped"},
  {text: "    self._call(call)", color: "accent" as const},
];
const CODE_H = Math.round(FONT * 1.35) + 2 * Math.round(FONT * 1.05) + LINES.length * Math.round(FONT * 1.55);
const HEAD = {top: 220, height: 390};

export const S06_LoopCode: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.frames - GLITCH} frames={GLITCH} seed={6}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(32)}, {at: T.at(34)}, {at: T.at(35)}]}>
          <Punch text="THE LOOP" at={T.at(32)} until={T.at(34)} size={220} zone={HEAD} />
          <Punch text={"~60\nTOOL CALLS"} at={T.at(34)} until={T.at(35)} size={190} zone={HEAD} />
          <Punch text={"NONE\nMALFORMED"} at={T.at(35)} until={T.frames} size={190} split={20} color={COLORS.accent} zone={HEAD} />
          <CodeReveal title="mafdet/agent/loop.py" lines={LINES} at={T.at(32)} frames={14} fontSize={FONT} style={{top: CODE_TOP}} />
          <Caption text="real sessions · Qwen3-14B · RTX 4070 SUPER" from={T.at(34)} top={CODE_TOP + CODE_H + 34} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
  </AbsoluteFill>
);
