// S08, beats 40-48: thinking ahead while the request is typed, the break, then Enter.
import React from "react";
import {AbsoluteFill, random, useCurrentFrame} from "remotion";
import {CodeReveal, FlashCut, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {MONO} from "../fonts";
import {Caption, Punch, Shake, ZONE, Zoom} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(40, 48);
const GLITCH = 5;

// The message box: a CodeReveal of two lines. Its proportions are CodeReveal's own.
const FONT = 40;
const LINE_H = Math.round(FONT * 1.55);
const PAD = Math.round(FONT * 1.05);
const TAB = Math.round(FONT * 1.35);
const BOX = {left: 110, width: 860};
const BOX_H = TAB + 2 * PAD + 2 * LINE_H + 2;
const BOX_TOP = ZONE.middle.top + (ZONE.middle.height - BOX_H) / 2;
const STATUS_TOP = BOX_TOP + TAB + 1 + PAD + LINE_H;
const HEAD = {top: 220, height: 460};

const PROMPT = "> ";
const REQUEST = "> read loop.py and explain it";
// When each character of the request is typed: uneven, a breath after every space. Beat 42 to just before 45.
const KEY_TIMES = (() => {
  const chars = REQUEST.slice(PROMPT.length).split("");
  const gaps = chars.map((_, i) => (i === 0 ? 0 : 0.5 + random(`s08-key-${i}`) + (chars[i - 1] === " " ? 1.6 : 0)));
  const total = gaps.reduce((a, b) => a + b, 0);
  const span = T.at(45) - 2 - T.at(42);
  let acc = 0;
  return gaps.map((g) => {
    acc += g;
    return T.at(42) + Math.round((acc / total) * span);
  });
})();
const TYPED_ALL = KEY_TIMES[KEY_TIMES.length - 1];

// The notes: 32 tokens at a time from beat 43, landing on the measured 273 on beat 45.
const tokens = (frame: number) => {
  if (frame >= T.at(45)) return 273;
  const chunk = (T.at(45) - T.at(43)) / 8;
  return 32 * Math.min(8, 1 + Math.floor((frame - T.at(43)) / chunk));
};

const MessageBox: React.FC = () => {
  const frame = useCurrentFrame();
  const typed = PROMPT.length + KEY_TIMES.filter((t) => t <= frame).length;
  const style = {...BOX, top: BOX_TOP};
  // CodeReveal types at a constant speed: the uneven rhythm is given by asking it, frame by frame, for the
  // number of characters typed so far. Before the typing and after it, it idles and its cursor blinks.
  const box =
    frame < T.at(42) ? (
      <CodeReveal title="mafdet agent" lines={[{text: PROMPT}, {text: ""}]} at={-T.frames} frames={1} fontSize={FONT} style={style} />
    ) : (
      <CodeReveal
        title="mafdet agent"
        lines={[{text: REQUEST}, {text: ""}]}
        at={Math.min(frame, TYPED_ALL) - typed}
        frames={REQUEST.length}
        fontSize={FONT}
        style={style}
      />
    );
  return (
    <>
      {box}
      {frame >= T.at(43) ? (
        <div
          style={{
            position: "absolute",
            left: BOX.left + 1 + PAD,
            top: STATUS_TOP,
            height: LINE_H,
            lineHeight: `${LINE_H}px`,
            fontFamily: MONO,
            fontWeight: 500,
            fontSize: FONT,
            whiteSpace: "pre",
            color: COLORS.accent,
          }}
        >
          {` · thinking ahead ${tokens(frame)} tok`}
        </div>
      ) : null}
    </>
  );
};

/** The box steps back when ENTER lands over it. */
const Under: React.FC<{children: React.ReactNode}> = ({children}) => {
  const frame = useCurrentFrame();
  return <div style={{position: "absolute", inset: 0, opacity: frame >= T.at(47) ? 0 : 1}}>{children}</div>;
};

export const S08_ThinkAhead: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.frames - GLITCH} frames={GLITCH} seed={8} intensity={1.7}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake hits={[{at: T.at(40)}, {at: T.at(41)}, {at: T.at(43), k: 0.5}, {at: T.at(47), k: 1.5}]}>
          <Punch text="IT THINKS" at={T.at(40)} until={T.at(41)} size={260} zone={HEAD} />
          <Punch text={"WHILE\nYOU TYPE"} at={T.at(41)} until={T.at(43)} size={220} color={COLORS.accent} zone={HEAD} />
          <Under>
            <Zoom from={T.at(45)} until={T.at(47)} to={1.08} originY={STATUS_TOP + LINE_H / 2}>
              <MessageBox />
            </Zoom>
          </Under>
          <Caption text="needs llama-server and a reasoning model (Qwen3)" from={T.at(42)} until={T.at(47)} />
          <Punch text="ENTER" at={T.at(47)} until={T.frames} size={380} split={24} color={COLORS.accent} />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
    <FlashCut at={T.at(40)} frames={1} />
    <FlashCut at={T.at(47)} />
  </AbsoluteFill>
);
