// S01, beats 0-4: the question, one phrase per beat.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {Punch, Shake} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(0, 4);
const GLITCH = 4;

export const S01_Hook: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.frames - GLITCH} frames={GLITCH} seed={1}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(0)}, {at: T.at(1)}, {at: T.at(2)}, {at: T.at(3), k: 1.5}]}>
          <Punch text={"YOUR\nBINARY"} at={T.at(0)} until={T.at(1)} size={240} />
          <Punch text={"YOUR\nMACHINE"} at={T.at(1)} until={T.at(2)} size={240} />
          <Punch text={"WHY\nSEND IT"} at={T.at(2)} until={T.at(3)} size={240} split={20} />
          <Punch text="ANYWHERE?" at={T.at(3)} until={T.frames} size={280} color={COLORS.accent} zoom={1.08} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
    <FlashCut at={T.at(0) + 1} />
  </AbsoluteFill>
);
