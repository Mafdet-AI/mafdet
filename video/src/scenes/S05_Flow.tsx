// S05, beats 20-32: the 7-step flow, one node per beat, the loop going back, then held to be read.
import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";
import {FlashCut, FlowDiagram, GlitchTransition} from "../components";
import {COLORS} from "../contracts";
import {Caption, Shake, Zoom} from "./a_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(20, 32);

const NODES = [
  {label: "TYPE", note: "model notes in 32-token chunks"},
  {label: "ENTER", note: "first step starts from the notes"},
  {label: "FIT CONTEXT", note: "old outputs left out or compacted"},
  {label: "MODEL STEP", note: "reasoning + text + tool calls"},
  {label: "CHECK", note: "args · tool exists · approval"},
  {label: "RUN TOOL", note: "output clipped and saved"},
  {label: "ANSWER", note: "no tool calls: the turn ends"},
];
const APPEAR = [20, 21, 22, 23, 24, 25, 28].map((b) => T.at(b));
const LOOP_BACK = {from: 5, to: 2, at: T.at(26)};
const BOX = {left: 110, top: 240, width: 860, height: 1140};

// The accent going round the loop: a quarter beat per node on beat 27, half a beat per node on 29-30.5.
const ROUND = [2, 3, 4, 5];
const half = (i: number) => T.at(29 + Math.floor(i / 2), (i % 2) / 2);
const CYCLE = [
  ...ROUND.map((node, i) => ({node, from: T.at(27, i / 4), until: T.at(27, (i + 1) / 4)})),
  ...ROUND.map((node, i) => ({node, from: half(i), until: half(i + 1)})),
];
// FlowDiagram gives the accent to the node that landed last: while the accent goes round, the diagram is
// the same one with every landing moved into the past and the node of the moment landing last.
const PAST = -T.frames;

const Diagram: React.FC = () => {
  const frame = useCurrentFrame();
  const step = CYCLE.find((c) => frame >= c.from && frame < c.until);
  if (!step) return <FlowDiagram nodes={NODES} appear={APPEAR} loopBack={LOOP_BACK} style={BOX} />;
  const appear = APPEAR.map((a, i) => (i === step.node ? PAST : a <= frame ? 2 * PAST : a));
  return <FlowDiagram nodes={NODES} appear={appear} loopBack={{...LOOP_BACK, at: 2 * PAST}} style={BOX} />;
};

export const S05_Flow: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <GlitchTransition at={T.at(26)} frames={4} seed={5}>
      <AbsoluteFill style={{backgroundColor: COLORS.black}}>
        <Shake
          hits={[
            {at: T.at(20)},
            {at: T.at(21), k: 0.5},
            {at: T.at(22), k: 0.5},
            {at: T.at(23), k: 0.5},
            {at: T.at(24)},
            {at: T.at(25), k: 0.5},
            {at: T.at(26), k: 1.5},
            {at: T.at(28)},
          ]}
        >
          <Zoom from={T.at(29)} until={T.frames} to={1.04} originY={BOX.top + BOX.height / 2}>
            <Diagram />
          </Zoom>
          <Caption text="mafdet/agent/loop.py · at most 40 steps" />
        </Shake>
      </AbsoluteFill>
    </GlitchTransition>
    {/* the 1-frame flash of the cut out of the drop */}
    <FlashCut at={T.at(20)} frames={1} />
    <FlashCut at={T.at(26)} frames={1} />
    <FlashCut at={T.at(28)} />
  </AbsoluteFill>
);
