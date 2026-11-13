// S14, beats 78-80: the run-up to the final hit. Four words on half beats, the frame inverting, then a breath of black.
import React from "react";
import {AbsoluteFill, useCurrentFrame} from "remotion";
import {PunchText} from "../components";
import {COLORS} from "../contracts";
import {Hold, Shake} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(78, 80);
const BREATH = 3; // frames of black before the hit

export const S14_Recap: React.FC = () => {
  const frame = useCurrentFrame();
  const end = T.frames - BREATH;
  const words: {text: string; at: number; color: string; split: number; inverted: boolean}[] = [
    {text: "TYPE", at: T.at(78), color: COLORS.white, split: 14, inverted: false},
    {text: "THINK", at: T.at(78, 0.5), color: COLORS.black, split: 0, inverted: true},
    {text: "CHECK", at: T.at(79), color: COLORS.white, split: 14, inverted: false},
    {text: "RUN", at: T.at(79, 0.5), color: COLORS.accent, split: 30, inverted: false},
  ];
  const until = (i: number) => (i + 1 < words.length ? words[i + 1].at : end);
  const inverted = words.some((w, i) => w.inverted && frame >= w.at && frame < until(i));

  return (
    <AbsoluteFill style={{backgroundColor: inverted ? COLORS.white : COLORS.black}}>
      <Shake hits={words.map((w) => ({at: w.at}))}>
        {words.map((w, i) => (
          <Hold key={w.text} from={w.at} until={until(i)}>
            <PunchText text={w.text} at={w.at} size={400} color={w.color} split={w.split} />
          </Hold>
        ))}
      </Shake>
    </AbsoluteFill>
  );
};
