// S10, beats 52-60: approvals in the real window. Shift+Tab through ask, edits, all on the status line, one
// a beat; then a command that asks first, y on beat 58, and it runs.
import React from "react";
import {AbsoluteFill} from "remotion";
import {tuiMark} from "../components";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake, ZONE} from "./a_shared";
import {LIVE_NOTE, Viewport} from "./live_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(52, 60);
const m = (name: string) => tuiMark("approve", name);

export const S10_Approvals: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Shake hits={[{at: T.at(52), k: 1.5}, {at: T.at(53)}, {at: T.at(54)}, {at: T.at(55)}, {at: T.at(56), k: 1.5},
                  {at: T.at(58), k: 1.5}, {at: T.at(59)}]}>
      <Viewport
        clip="approve"
        at={T.at(52)}
        keys={[
          {frame: T.at(52), t: m("mode:edits") - 0.1},
          {frame: T.at(53) - 1, t: m("mode:edits") - 0.05},
          {frame: T.at(53), t: m("mode:edits") + 0.05},
          {frame: T.at(54) - 1, t: m("mode:all") - 0.05},
          {frame: T.at(54), t: m("mode:all") + 0.05},
          {frame: T.at(55) - 1, t: m("mode:ask") - 0.05},
          {frame: T.at(55), t: m("mode:ask") + 0.05},
          {frame: T.at(56), t: m("enter")},
          {frame: T.at(57), t: m("asked") + 0.2},
          {frame: T.at(58) - 1, t: m("yes") - 0.05},
          {frame: T.at(58), t: m("yes") + 0.05},
          {frame: T.at(59), t: m("answer:start") + 0.2},
          {frame: T.frames, t: m("done") + 0.4},
        ]}
        shots={[
          {frame: 0, x: 330, y: 1440, scale: 3.0},            // the status line: the mode
          {frame: T.at(56), x: 610, y: 1000, scale: 1.0},     // the request and the call
          {frame: T.at(57), x: 340, y: 1300, scale: 2.2},     // "Run it?  y yes  a always  n no"
          {frame: T.at(59), x: 560, y: 720, scale: 1.4},      // it ran: the output, the answer
        ]}
      />
      <Punch text="ASK" at={T.at(52)} until={T.at(53)} size={300} zone={ZONE.top} />
      <Punch text="EDITS" at={T.at(53)} until={T.at(54)} size={300} zone={ZONE.top} />
      <Punch text="ALL" at={T.at(54)} until={T.at(55)} size={300} color={COLORS.accent} zone={ZONE.top} />
      <Punch text="SHIFT+TAB" at={T.at(55)} until={T.at(56)} size={240} color={COLORS.hot} zone={ZONE.top} />
      <Punch text="IT ASKS FIRST" at={T.at(56)} until={T.at(58)} size={190} zone={ZONE.top} />
      <Punch text="Y" at={T.at(58)} until={T.at(59)} size={300} color={COLORS.hot} zone={ZONE.top} />
      <Punch text="IT RUNS" at={T.at(59)} until={T.frames} size={260} color={COLORS.accent} zone={ZONE.top} />
      <Caption text="ask · edits · all: what runs without asking you" from={T.at(52)} until={T.at(56)} />
      <Caption text={LIVE_NOTE} from={T.at(56)} />
    </Shake>
  </AbsoluteFill>
);
