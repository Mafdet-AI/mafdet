// S07, beats 36-40: what goes wrong is told back to the model. One phrase per beat over a still terminal.
import React from "react";
import {AbsoluteFill} from "remotion";
import {CodeReveal, FlashCut} from "../components";
import {COLORS} from "../contracts";
import {Punch, Shake} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(36, 40);
const CODE_TOP = 1110;
const MAIN = {top: 540, height: 540};

export const S07_SelfCorrect: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Shake hits={[{at: T.at(36)}, {at: T.at(37)}, {at: T.at(38)}, {at: T.at(39), k: 1.5}]}>
      <Punch text={"BAD\nJSON"} at={T.at(36)} until={T.at(37)} size={280} zone={MAIN} />
      <Punch text={"UNKNOWN\nTOOL"} at={T.at(37)} until={T.at(38)} size={260} zone={MAIN} />
      <Punch text={"DECLINED\nCALL"} at={T.at(38)} until={T.at(39)} size={260} zone={MAIN} />
      <Punch text={"TOLD\nBACK"} at={T.at(39)} until={T.frames} size={280} split={24} color={COLORS.accent} zoom={1.08} zone={MAIN} />
      <CodeReveal
        title="told back to the model"
        lines={[{text: "The user declined this call. Do not"}, {text: "repeat it: go on another way,"}]}
        at={T.at(36)}
        frames={12}
        fontSize={38}
        style={{top: CODE_TOP}}
      />
    </Shake>
    <FlashCut at={T.at(36)} />
  </AbsoluteFill>
);
