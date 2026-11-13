// S08, beats 40-48: thinking ahead, in the real window. The request is typed while the status line counts
// what the model reasons on it; the camera goes into that count during the break; ENTER on the pickup.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, tuiMark} from "../components";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake, ZONE} from "./a_shared";
import {Viewport} from "./live_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(40, 48);
const m = (name: string) => tuiMark("ahead", name);

export const S08_ThinkAhead: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Shake hits={[{at: T.at(40)}, {at: T.at(42)}, {at: T.at(44), k: 0.5}, {at: T.at(47), k: 1.5}]}>
      <Viewport
        clip="ahead"
        at={T.at(40)}
        keys={[
          {frame: T.at(40), t: m("type")},
          {frame: T.at(44), t: m("typed")},
          {frame: T.at(47) - 1, t: m("typed") + 0.6},
          {frame: T.at(47), t: m("enter")},
          {frame: T.frames, t: m("enter") + 0.9},
        ]}
        shots={[
          {frame: 0, x: 600, y: 1410, scale: 1.6},            // the message box and the status line
          {frame: T.at(44), x: 690, y: 1445, scale: 3.2},     // "thinking ahead 273 tok"
          {frame: T.at(47), x: 600, y: 760, scale: 1.3},      // sent: the request, the model at work
        ]}
      />
      <Punch text="IT THINKS" at={T.at(40)} until={T.at(42)} size={230} zone={ZONE.top} />
      <Punch text="WHILE YOU TYPE" at={T.at(42)} until={T.at(45)} size={190} color={COLORS.hot} zone={ZONE.top} />
      <Punch text="ENTER" at={T.at(47)} until={T.frames} size={300} split={24} color={COLORS.accent} zone={ZONE.top} />
      <Caption text="the status line counts what it has reasoned" from={T.at(40)} until={T.at(44)} />
      <Caption text="needs llama-server and a reasoning model (Qwen3)" from={T.at(44)} until={T.at(47)} />
      <Caption text="the first step starts from those notes" from={T.at(47)} />
    </Shake>
    <FlashCut at={T.at(47)} />
  </AbsoluteFill>
);
