// S03, beats 12-16: one command, the screen emptying with the bass, then the pickup into the drop.
import React from "react";
import {AbsoluteFill} from "remotion";
import {CodeReveal, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {Punch, Shake, ZONE, Zoom} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(12, 16);
const GLITCH = 5;
const FONT = 60;
// the terminal's height: tab + two paddings + one line (CodeReveal's own proportions)
const BOX_H = Math.round(FONT * 1.35) + 2 * Math.round(FONT * 1.05) + Math.round(FONT * 1.55);
const MID = ZONE.middle.top + ZONE.middle.height / 2;

export const S03_Command: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.frames - GLITCH} frames={GLITCH} seed={3} intensity={1.7}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(12)}, {at: T.at(15), k: 1.5}]}>
          <Punch text="ONE COMMAND" at={T.at(12)} until={T.at(13)} size={180} zone={ZONE.top} />
          <Zoom from={T.at(13)} until={T.at(15)} to={1.05} originY={MID}>
            <CodeReveal
              title="terminal"
              lines={[{text: "> mafdet agent"}]}
              at={T.at(12)}
              frames={12}
              fontSize={FONT}
              style={{top: MID - BOX_H / 2}}
            />
          </Zoom>
          <Punch text="NOW WATCH" at={T.at(15)} until={T.frames} size={240} color={COLORS.accent} zone={ZONE.top} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
  </AbsoluteFill>
);
