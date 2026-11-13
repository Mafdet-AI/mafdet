// S07, beats 36-40: a whole turn in the real window, one event a beat: the request goes in, read_file
// comes back, the edit shows as a diff, the answer.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, tuiMark} from "../components";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake, ZONE} from "./a_shared";
import {LIVE_NOTE, Viewport} from "./live_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(36, 40);
const m = (name: string) => tuiMark("turn", name);

export const S07_Live: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Shake hits={[{at: T.at(36), k: 1.5}, {at: T.at(37)}, {at: T.at(38)}, {at: T.at(39)}]}>
      <Viewport
        clip="turn"
        at={T.at(36)}
        keys={[
          {frame: T.at(36), t: m("enter") - 0.05},
          {frame: T.at(37), t: m("read:end") + 0.1},
          {frame: T.at(38), t: m("edit:end") + 0.1},
          {frame: T.at(39), t: m("answer:end")},
          {frame: T.frames, t: m("done") + 0.3},
        ]}
        shots={[
          {frame: 0, x: 560, y: 640, scale: 1.5},             // the request going in
          {frame: T.at(37), x: 420, y: 700, scale: 1.75},      // read_file and what it read
          {frame: T.at(38), x: 330, y: 895, scale: 2.2},      // the diff
          {frame: T.at(39), x: 470, y: 1010, scale: 1.45},    // the answer
        ]}
      />
      <Punch text="WATCH IT WORK" at={T.at(36)} until={T.at(37)} size={190} zone={ZONE.top} />
      <Punch text="READS" at={T.at(37)} until={T.at(38)} size={250} zone={ZONE.top} />
      <Punch text="EDITS" at={T.at(38)} until={T.at(39)} size={250} color={COLORS.accent} zone={ZONE.top} />
      <Punch text="ANSWERS" at={T.at(39)} until={T.frames} size={250} color={COLORS.hot} zone={ZONE.top} />
      <Caption text={LIVE_NOTE} from={T.at(36)} />
    </Shake>
    <FlashCut at={T.at(36)} frames={1} />
  </AbsoluteFill>
);
