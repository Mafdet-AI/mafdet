// S15, beats 80-88 (to the last frame): the theatrical close.
//   80  the hit: the cheetah, MAFDET's mascot, slams in with its eyes lit
//   81  the wordmark uncovers under it
//   82  letterbox bars close in, a slow push
//   83  black: only the eyes
//   84  AHEAD,   85  TOGETHER.
//   86  the final card: the cheetah, the slogan, the wordmark; the cheetah blinks; all fades but the eyes,
//       which go out last.
import React from "react";
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from "remotion";
import {FlashCut, GlitchTransition, PixelCheetah, PunchText, Wordmark} from "../components";
import {COLORS, SIZE} from "../contracts";
import {MONO} from "../fonts";
import {Shake} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(80, 88);
const clamp = {extrapolateLeft: "clamp", extrapolateRight: "clamp"} as const;
const out = {...clamp, easing: Easing.out(Easing.cubic)};

const Centred: React.FC<{top: number; width: number; children: React.ReactNode; style?: React.CSSProperties}> = ({
  top, width, children, style,
}) => <div style={{position: "absolute", left: (SIZE.width - width) / 2, top, width, ...style}}>{children}</div>;

export const S15_Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const b = (n: number, f = 0) => T.at(n, f);
  const end = T.frames;

  // 80-83: the mascot and the wordmark
  const hero = 720;
  const heroW = (hero / 20) * 19;
  const slam = interpolate(frame, [0, 4], [1.3, 1], out);
  const push = interpolate(frame, [b(82), b(83)], [1, 1.07], clamp);
  const wordReveal = interpolate(frame, [b(81), b(81) + 6], [0, 1], out);
  const bars = interpolate(frame, [b(82), b(82) + 8], [0, 230], out);

  // 86 on: the final card
  const card = frame >= b(86);
  const cardIn = interpolate(frame, [b(86), b(86) + 6], [0, 1], out);
  const fade = interpolate(frame, [end - 16, end - 5], [1, 0], clamp);          // everything but the eyes
  const eyesOut = interpolate(frame, [end - 4, end - 1], [1, 0], clamp);        // the eyes, last
  const blink = card && frame >= b(87) && frame < b(87) + 3;

  const slogan = (top: number, size: number, opacity = 1) => (
    <div style={{opacity}}>
      <PunchText text="AHEAD," at={b(84)} size={size} split={22} style={{top}} />
      {frame >= b(85) ? (
        <PunchText text="TOGETHER." at={b(85)} size={size} split={26} color={COLORS.hot} style={{top: top + size * 1.02}} />
      ) : null}
    </div>
  );

  return (
    <AbsoluteFill style={{backgroundColor: COLORS.black}}>
      <Shake hits={[{at: 0, k: 2}, {at: b(81), k: 0.5}, {at: b(84), k: 1.5}, {at: b(85), k: 1.5}, {at: b(86), k: 0.5}]}>
        {frame < b(83) ? (
          <GlitchTransition at={0} frames={6} seed={80} intensity={1.8}>
            <AbsoluteFill style={{transform: `scale(${push.toFixed(4)})`, transformOrigin: "50% 45%"}}>
              <Centred top={250} width={heroW} style={{transform: `scale(${slam.toFixed(4)})`, transformOrigin: "50% 60%"}}>
                <PixelCheetah height={hero} glow={1} tail={frame >= b(82) ? 1 : 0} />
              </Centred>
              <Centred top={1060} width={880}>
                <Wordmark width={880} reveal={wordReveal} />
              </Centred>
              {frame >= b(82) ? (
                <Centred top={1240} width={880}>
                  <div style={{fontFamily: MONO, fontSize: 34, letterSpacing: "0.32em", color: COLORS.accent,
                               textAlign: "center", opacity: interpolate(frame, [b(82), b(82) + 4], [0, 1], clamp)}}>
                    AGENT
                  </div>
                </Centred>
              ) : null}
            </AbsoluteFill>
          </GlitchTransition>
        ) : frame < b(86) ? (
          // 83-86: black, the eyes; then the slogan
          <AbsoluteFill>
            <Centred top={250} width={heroW} style={{transform: "scale(1.07)", transformOrigin: "50% 60%"}}>
              <PixelCheetah height={hero} eyesOnly glow={1} />
            </Centred>
            {slogan(560, 300)}
          </AbsoluteFill>
        ) : (
          // 86 on: the final card
          <AbsoluteFill>
            <div style={{opacity: fade}}>
              <Centred top={260 - (1 - cardIn) * 40} width={(320 / 20) * 19}>
                <PixelCheetah height={320} glow={0.8} blink={blink} />
              </Centred>
            </div>
            {fade < 1 ? (
              // the eyes outlive the rest
              <div style={{opacity: eyesOut}}>
                <Centred top={260} width={(320 / 20) * 19}>
                  <PixelCheetah height={320} eyesOnly glow={0.9} />
                </Centred>
              </div>
            ) : null}
            <div style={{opacity: fade}}>
              {slogan(700, 230)}
              <Centred top={1250} width={620} style={{opacity: cardIn}}>
                <Wordmark width={620} />
              </Centred>
              <Centred top={1360} width={900} style={{opacity: cardIn}}>
                <div style={{fontFamily: MONO, fontSize: 28, color: COLORS.soft, textAlign: "center", letterSpacing: "0.04em"}}>
                  mafdet agent · experimental
                </div>
              </Centred>
            </div>
          </AbsoluteFill>
        )}
      </Shake>
      {/* the letterbox: from 82 to the end */}
      <div style={{position: "absolute", left: 0, right: 0, top: 0, height: bars, background: COLORS.black}} />
      <div style={{position: "absolute", left: 0, right: 0, bottom: 0, height: bars, background: COLORS.black}} />
      {frame >= b(82) ? (
        <>
          <div style={{position: "absolute", left: 0, right: 0, top: bars, height: 2, background: COLORS.accent, opacity: 0.6 * fade}} />
          <div style={{position: "absolute", left: 0, right: 0, bottom: bars, height: 2, background: COLORS.accent, opacity: 0.6 * fade}} />
        </>
      ) : null}
      <FlashCut at={0} frames={2} color={COLORS.hot} />
      <FlashCut at={b(84)} frames={1} />
      <FlashCut at={b(85)} frames={1} color={COLORS.hot} />
    </AbsoluteFill>
  );
};
