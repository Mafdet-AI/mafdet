// S09, beats 48-52: the relaunch. The measured number is the hit.
import React from "react";
import {AbsoluteFill} from "remotion";
import {FlashCut, GlitchTransition, PunchText} from "../components";
import {COLORS} from "../contracts";
import {Caption, Hold, Shake, WIDTH, Y, Zoom, fitSize, lineTop} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(48, 52);
const ZOOM = 1.1;

export const S09_Numbers: React.FC = () => {
  const b48 = T.at(48);
  const b49 = T.at(49);
  const b50 = T.at(50);
  const b51 = T.at(51);
  const under = WIDTH / ZOOM; // the zoomed number still ends inside the safe area
  const underBottom = Y.middle + (fitSize("UNDER 2 S", 340, under) * ZOOM) / 2;
  const upBottom = Y.middle + fitSize("UP TO 5.5 S", 300) / 2;

  return (
    <AbsoluteFill>
      <Shake hits={[{at: b48, k: 2}, {at: b49}, {at: b50}, {at: b51}]}>
        <GlitchTransition at={b48} frames={6} seed={48} intensity={1.6}>
          <AbsoluteFill>
            <Hold until={b50}>
              <Zoom from={b49} to={b50} scale={ZOOM} cy={Y.middle}>
                <PunchText
                  text="UNDER 2 S"
                  at={b48}
                  size={340}
                  color={COLORS.accent}
                  split={28}
                  maxWidth={under}
                  style={{top: lineTop("UNDER 2 S", 340, Y.middle, under)}}
                />
              </Zoom>
              <Caption text="first tool call after Enter, with notes" top={underBottom + 26} color={COLORS.white} />
            </Hold>
            <Hold from={b50} until={b51}>
              <PunchText text="WITHOUT NOTES" at={b50} size={240} style={{top: lineTop("WITHOUT NOTES", 240, Y.middle)}} />
            </Hold>
            <Hold from={b51}>
              <PunchText
                text="UP TO 5.5 S"
                at={b51}
                size={300}
                color={COLORS.dim}
                split={20}
                style={{top: lineTop("UP TO 5.5 S", 300, Y.middle)}}
              />
              <Caption text="first tool call after Enter, without notes" top={upBottom + 26} from={b51} color={COLORS.white} />
            </Hold>
            <Caption text={["Qwen3-4B · RTX 2060 · 3 runs", "a direction, not a result"]} top={Y.caption2} />
          </AbsoluteFill>
        </GlitchTransition>
      </Shake>
      <FlashCut at={b48} frames={2} />
    </AbsoluteFill>
  );
};
