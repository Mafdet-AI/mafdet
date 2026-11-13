// S04, beats 16-20: THE DROP. The real window slams in, the name over it; then the camera goes into the
// banner: the cheetah, the mascot, taps its ball on beat 19.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, GlitchTransition, tuiKitten} from "../components";
import {COLORS} from "../contracts";
import {Caption, Punch, Shake, ZONE} from "./a_shared";
import {Viewport} from "./live_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(16, 20);
const TAP = tuiKitten(30); // the banner's paw reaches the ball from loop frame 30

export const S04_Drop: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.at(16)} frames={6} seed={4} intensity={1.6}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(16), k: 2}, {at: T.at(17)}, {at: T.at(18), k: 1.5}, {at: T.at(19)}]}>
          <Viewport
            clip="banner"
            at={T.at(16)}
            keys={[{frame: 0, t: TAP - T.at(19) / 30}, {frame: T.at(19), t: TAP}]}
            shots={[
              {frame: 0, x: 619, y: 733, scale: 1.3},
              {frame: T.at(16), x: 619, y: 560, scale: 0.92},     // the window, slammed in
              {frame: T.at(18), x: 222, y: 190, scale: 3.5},      // into the banner: the cheetah and its ball
            ]}
          />
          <Punch text="MAFDET" at={T.at(16)} until={T.at(17)} size={250} split={28} zone={ZONE.top} />
          <Punch text="AGENT" at={T.at(17)} until={T.at(18)} size={250} split={20} color={COLORS.accent} zone={ZONE.top} />
          <Punch text="THE CHEETAH" at={T.at(18)} until={T.at(19)} size={210} zone={ZONE.top} />
          <Punch text="HUNTS AHEAD" at={T.at(19)} until={T.frames} size={210} color={COLORS.hot} zone={ZONE.top} />
          <Caption text="the real mafdet agent window" from={T.at(16)} until={T.at(18)} />
          <Caption text="its mascot, in the banner" from={T.at(18)} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
    <FlashCut at={T.at(16)} frames={2} />
  </AbsoluteFill>
);
