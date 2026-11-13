// S13, beats 72-78: --walk, every function of a binary named from the leaves up.
import React from "react";
import {AbsoluteFill} from "remotion";
import {CodeReveal, FlashCut, PunchText} from "../components";
import {COLORS} from "../contracts";
import {Caption, Hold, Shake, Y, lineTop} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(72, 78);

const Top: React.FC<{text: string; size: number; at: number; until: number; color?: string; split?: number}> = ({
  text,
  size,
  at,
  until,
  color,
  split,
}) => (
  <Hold from={at} until={until}>
    <PunchText text={text} at={at} size={size} color={color} split={split} style={{top: lineTop(text, size, Y.top)}} />
  </Hold>
);

export const S13_Walk: React.FC = () => {
  const b = (n: number) => T.at(n);
  return (
    <AbsoluteFill>
      <Shake hits={[{at: b(72)}, {at: b(73)}, {at: b(74)}, {at: b(75)}, {at: b(76), k: 1.5}, {at: b(77)}]}>
        <Top text="--WALK" size={340} at={b(72)} until={b(73)} color={COLORS.accent} />
        <Top text="EVERY FUNCTION" size={240} at={b(73)} until={b(74)} />
        <Top text="LEAVES UP" size={300} at={b(74)} until={b(75)} />
        <Top text="ONE AT A TIME" size={240} at={b(75)} until={b(76)} />
        <Top text="86 OF 86" size={360} at={b(76)} until={b(77)} color={COLORS.accent} split={24} />
        <Top text="0 FAILED" size={320} at={b(77)} until={T.frames} />

        {/* the command leaves when the numbers arrive: they are of another run (where.exe, no cap) */}
        <Hold until={b(76)}>
          <CodeReveal
            title="terminal"
            lines={[{text: "mafdet agent --walk C:\\samples\\crackme.exe"}, {text: "  --mcp rea --max-functions 50"}]}
            at={b(72)}
            frames={16}
            fontSize={32}
            style={{top: 760}}
          />
        </Hold>

        <Caption text="of a binary, named from the leaves up" top={Y.caption} from={b(73)} until={b(75)} />
        <Caption text="code drives the walk, the model gets one function" top={Y.caption} from={b(75)} until={b(76)} />
        <Caption
          text={["where.exe · functions named · about 25 min", "named, not graded: accuracy was not measured"]}
          top={Y.top + 190}
          from={b(76)}
          color={COLORS.white}
        />
      </Shake>
      <FlashCut at={b(72)} frames={2} />
    </AbsoluteFill>
  );
};
