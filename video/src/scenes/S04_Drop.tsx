// S04, beats 16-20: THE DROP. The name, then seven steps, one loop.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {Punch, Shake, ZONE} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(16, 20);
const NAME = 310; // MAFDET at 340 is fitted down to this by PunchText: AGENT matches it
const HALF = ZONE.middle.height / 2;
const UPPER = {top: ZONE.middle.top, height: HALF};
const LOWER = {top: ZONE.middle.top + HALF, height: HALF};

export const S04_Drop: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.at(16)} frames={6} seed={4} intensity={1.6}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(16), k: 2}, {at: T.at(17)}, {at: T.at(18)}, {at: T.at(19)}]}>
          <Punch text="MAFDET" at={T.at(16)} until={T.at(18)} size={NAME} split={28} zone={UPPER} />
          <Punch text="AGENT" at={T.at(17)} until={T.at(18)} size={NAME} split={20} color={COLORS.accent} zone={LOWER} />
          <Punch text={"SEVEN\nSTEPS"} at={T.at(18)} until={T.at(19)} size={260} />
          <Punch text={"ONE\nLOOP"} at={T.at(19)} until={T.frames} size={300} color={COLORS.accent} zoom={1.1} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
    <FlashCut at={T.at(16)} frames={2} />
  </AbsoluteFill>
);
