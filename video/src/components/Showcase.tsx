import React from "react";
import {AbsoluteFill, Sequence} from "remotion";
import {COLORS, SAFE, SIZE} from "../contracts";
import {MONO} from "../fonts";
import {CodeReveal} from "./CodeReveal";
import {FlashCut} from "./FlashCut";
import {FlowDiagram} from "./FlowDiagram";
import {GlitchTransition} from "./GlitchTransition";
import {GrainOverlay} from "./GrainOverlay";
import {PunchText} from "./PunchText";
import {ShakeOnBeat} from "./ShakeOnBeat";

/** 240 frames, something hits every 14. Not part of the edit: it is the test card of the component library. */
export const SHOWCASE_FRAMES = 240;
const STEP = 14;
const HITS = Array.from({length: Math.ceil(SHOWCASE_FRAMES / STEP)}, (_, i) => i * STEP);

const Rule: React.FC<{top: number; label: string}> = ({top, label}) => (
  <div
    style={{
      position: "absolute",
      left: SAFE.side,
      width: SIZE.width - 2 * SAFE.side,
      top,
      borderTop: `1px solid ${COLORS.line}`,
      paddingTop: 12,
      fontFamily: MONO,
      fontWeight: 500,
      fontSize: 22,
      letterSpacing: "0.16em",
      color: COLORS.dim,
      textTransform: "uppercase",
    }}
  >
    {label}
  </div>
);

export const Showcase: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: COLORS.black}}>
      <ShakeOnBeat hits={HITS}>
        {/* 0-55: PunchText, one word per hit, the last one long enough to need the fit */}
        <Sequence durationInFrames={4 * STEP}>
          <GlitchTransition at={3 * STEP} frames={5} seed={1}>
            <AbsoluteFill>
              <Rule top={SAFE.top} label="punchtext / flashcut / shake" />
              <PunchText text="it reads" at={0} style={{top: 520}} />
              <PunchText text="it thinks" at={STEP} color={COLORS.accent} style={{top: 760}} />
              <PunchText text="ahead" at={2 * STEP} size={300} style={{top: 1000}} />
              <PunchText text="while you are still typing" at={3 * STEP} style={{top: 1330}} />
            </AbsoluteFill>
          </GlitchTransition>
        </Sequence>

        {/* 56-125: CodeReveal, cut in through a glitch */}
        <Sequence from={4 * STEP} durationInFrames={5 * STEP}>
          <GlitchTransition at={0} frames={5} seed={2}>
            <AbsoluteFill>
              <Rule top={SAFE.top} label="codereveal / glitchtransition" />
              <CodeReveal
                title="mafdet agent"
                at={2}
                frames={3 * STEP - 4}
                style={{top: 420}}
                lines={[
                  {text: "$ mafdet agent ./firmware.bin", color: "white"},
                  {text: "> open_binary   arm64, 2.1 MB", color: "dim"},
                  {text: "> list_strings  412 found", color: "dim"},
                  {text: "> decompile     sub_4010a0", color: "dim"},
                  {text: "thinking ahead: 3 branches", color: "accent"},
                  {text: "answer ready before Enter", color: "white"},
                ]}
              />
              <PunchText text="no wait" at={3 * STEP} color={COLORS.accent} style={{top: 1010}} />
              <PunchText text="zero" at={4 * STEP} size={320} style={{top: 1230}} />
            </AbsoluteFill>
          </GlitchTransition>
        </Sequence>

        {/* 126-223: FlowDiagram, a node per hit, then the loop */}
        <Sequence from={9 * STEP} durationInFrames={7 * STEP}>
          <GlitchTransition at={0} frames={4} seed={3}>
            <AbsoluteFill>
              <Rule top={SAFE.top} label="flowdiagram" />
              <FlowDiagram
                style={{top: 330}}
                nodes={[
                  {label: "you type", note: "every keystroke is read"},
                  {label: "prefill", note: "the prompt is already in the cache"},
                  {label: "think ahead", note: "reasoning starts before Enter"},
                  {label: "tool call", note: "open, decompile, trace"},
                  {label: "answer"},
                ]}
                appear={[0, STEP, 2 * STEP, 3 * STEP, 4 * STEP]}
                loopBack={{from: 3, to: 2, at: 5 * STEP}}
              />
              <PunchText text="the loop" at={6 * STEP} size={150} color={COLORS.accent} style={{top: 1330}} />
            </AbsoluteFill>
          </GlitchTransition>
        </Sequence>

        {/* 224-239: the sign-off through a hard glitch */}
        <Sequence from={16 * STEP}>
          <GlitchTransition at={0} frames={6} seed={4} intensity={1.4}>
            <AbsoluteFill>
              <PunchText text="mafdet" at={0} size={380} split={22} />
            </AbsoluteFill>
          </GlitchTransition>
        </Sequence>
      </ShakeOnBeat>

      {HITS.map((h) => (
        <FlashCut key={h} at={h} color={h % (4 * STEP) === 2 * STEP ? COLORS.accent : undefined} />
      ))}
      <GrainOverlay />
    </AbsoluteFill>
  );
};
