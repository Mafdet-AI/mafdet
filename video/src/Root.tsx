import React from "react";
import {Composition} from "remotion";
import {Edit} from "./Edit";
import {SIZE} from "./contracts";
import {beats} from "./timing";

export const Root: React.FC = () => (
  <Composition id="Edit" component={Edit} width={SIZE.width} height={SIZE.height} fps={beats.fps}
               durationInFrames={beats.durationInFrames} />
);
