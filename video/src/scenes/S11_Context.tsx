// S11, beats 60-64: the context that fits, one number per beat.
import React from "react";
import {AbsoluteFill} from "remotion";
import {PunchText} from "../components";
import {COLORS} from "../contracts";
import {Caption, Hold, Shake, Y, fitSize, lineTop} from "./b_shared";
import {sceneTiming} from "./sceneTiming";

const T = sceneTiming(60, 64);

/** One main text centred in MIDDLE with its caption directly under it. */
const Figure: React.FC<{text: string; size: number; color?: string; caption: string; at: number; until: number}> = ({
  text,
  size,
  color,
  caption,
  at,
  until,
}) => (
  <Hold from={at} until={until}>
    <PunchText text={text} at={at} size={size} color={color} style={{top: lineTop(text, size, Y.middle)}} />
    <Caption text={caption} top={Y.middle + fitSize(text, size) / 2 + 26} from={at} color={COLORS.white} />
  </Hold>
);

export const S11_Context: React.FC = () => {
  const b60 = T.at(60);
  const b61 = T.at(61);
  const b62 = T.at(62);
  const b63 = T.at(63);

  return (
    <AbsoluteFill>
      <Shake hits={[{at: b60}, {at: b61}, {at: b62, k: 1.5}, {at: b63}]}>
        <Figure text="4,000 CHARS" size={236} caption="each tool output is cut, and kept whole on disk" at={b60} until={b61} />
        {/* one number a beat, their conditions held under both so they can be read */}
        <Hold from={b61} until={b62}>
          <PunchText text="4,496" at={b61} size={380} color={COLORS.dim} style={{top: lineTop("4,496", 380, Y.middle)}} />
        </Hold>
        <Hold from={b62} until={b63}>
          <PunchText text="2,359" at={b62} size={380} color={COLORS.accent} style={{top: lineTop("2,359", 380, Y.middle)}} />
        </Hold>
        <Caption
          text={["tokens, before and after compaction", "6k context · the task finished right"]}
          top={Y.middle + fitSize("4,496", 380) / 2 + 26}
          from={b61}
          until={b63}
          color={COLORS.white}
        />
        <Figure text="NOTES STAY" size={300} caption="notes and the to-do list are never left out" at={b63} until={T.frames} />
      </Shake>
    </AbsoluteFill>
  );
};
