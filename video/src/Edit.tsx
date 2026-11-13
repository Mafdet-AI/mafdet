// The edit: every scene of storyboard.json in its place on the beat grid, the track under it, film on top.
import React from "react";
import {AbsoluteFill, Audio, Sequence, staticFile} from "remotion";
import {GrainOverlay} from "./components";
import {COLORS} from "./contracts";
import {blockA} from "./scenes/blockA";
import {blockB} from "./scenes/blockB";
import storyboard from "./storyboard.json";
import {beat, beats} from "./timing";

type Entry = {id: string; file: string; component: string; fromBeat: number; toBeat: number; block: "A" | "B"};
const scenes: Record<string, React.FC> = {...blockA, ...blockB};

export const Edit: React.FC = () => (
  <AbsoluteFill style={{backgroundColor: COLORS.black}}>
    <Audio src={staticFile("audio/track.wav")} />
    {(storyboard as Entry[]).map((s) => {
      const from = beat(s.fromBeat);
      const to = s.toBeat >= beats.beats.length ? beats.durationInFrames : beat(s.toBeat);
      const Scene = scenes[s.component];
      return (
        <Sequence key={s.id} from={from} durationInFrames={to - from} name={s.file}>
          {Scene ? <Scene /> : null}
        </Sequence>
      );
    })}
    <GrainOverlay />
  </AbsoluteFill>
);
