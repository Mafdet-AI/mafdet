// S15, beats 80-88 (to the last frame): the final hit, the command, the motto, then black.
import React from "react";
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from "remotion";
import {CodeReveal, FlashCut, GlitchTransition, PunchText} from "../components";
import {COLORS} from "../contracts";
import {Caption, Hold, Shake, WIDTH, Y, fitSize} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(80, 88);
const CLAMP = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;
const ZOOM = 1.05;
const SLIDE = 6; // frames the name takes to move up
const FADE = 12; // frames of the final fade
const TITLE = {top: 226, size: 150}; // the name once it sits in TOP
const HIT_CY = 800; // centre of the name on the hit
const MOTTO = {top: 962, size: 180};

export const S15_Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const b = (n: number) => T.at(n);

  // The name: two lines of one size, zooming through beat 81, then up into TOP as a title.
  const size = fitSize("MAFDET", 340, WIDTH / ZOOM);
  const step = Math.round(size * 1.02);
  const height = size + step;
  const small = TITLE.size / size;
  const p = interpolate(frame, [b(82), b(82) + SLIDE], [0, 1], {...CLAMP, easing: Easing.out(Easing.cubic)});
  const zoom = interpolate(frame, [b(81), b(82)], [1, ZOOM], CLAMP);
  const scale = zoom + (small - zoom) * p;
  const dy = (TITLE.top + (height * small) / 2 - HIT_CY) * p;

  const fade = interpolate(frame, [T.frames - FADE, T.frames - 1], [1, 0], CLAMP);
  const motto = Math.min(fitSize("STOP WAITING.", MOTTO.size), fitSize("START TYPING.", MOTTO.size));

  return (
    <AbsoluteFill>
      <Shake hits={[{at: b(80), k: 2}, {at: b(81)}, {at: b(82), k: 0.5}, {at: b(84)}, {at: b(85)}]}>
        <GlitchTransition at={b(80)} frames={6} seed={80} intensity={1.8}>
          <AbsoluteFill>
            {/* the command: the only thing left on the last frame */}
            <Hold from={b(82) + SLIDE}>
              <CodeReveal title="terminal" lines={[{text: "> mafdet agent"}]} at={b(82) + SLIDE} frames={12} fontSize={44} style={{top: 640}} />
            </Hold>

            {/* the name and the command stay to the last frame; the motto and the caption fade */}
            <AbsoluteFill>
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  right: 0,
                  top: HIT_CY - height / 2,
                  height,
                  transformOrigin: "50% 50%",
                  transform: `translateY(${dy.toFixed(2)}px) scale(${scale.toFixed(4)})`,
                }}
              >
                <PunchText text="MAFDET" at={b(80)} size={size} split={30} style={{top: 0}} />
                <PunchText text="AGENT" at={b(80)} size={size} color={COLORS.accent} split={30} style={{top: step}} />
              </div>

            </AbsoluteFill>
            <AbsoluteFill style={{opacity: fade}}>
              <PunchText text="STOP WAITING." at={b(84)} size={motto} style={{top: MOTTO.top}} />
              <PunchText
                text="START TYPING."
                at={b(85)}
                size={motto}
                color={COLORS.accent}
                split={20}
                style={{top: MOTTO.top + Math.round(motto * 1.06)}}
              />
              <Caption text="experimental · a local model, on your machine" top={Y.caption} from={b(82) + SLIDE} />
            </AbsoluteFill>
          </AbsoluteFill>
        </GlitchTransition>
      </Shake>
      <FlashCut at={b(80)} frames={2} />
    </AbsoluteFill>
  );
};
