// A temporary entry to look at the component library alone, without Root.tsx:
//   npx remotion still src/_showcase.entry.tsx Showcase ../out/showcase_30.png --frame=30
import React from "react";
import {Composition, registerRoot} from "remotion";
import {Showcase, SHOWCASE_FRAMES} from "./components/Showcase";

const ShowcaseRoot: React.FC = () => (
  <Composition id="Showcase" component={Showcase} width={1080} height={1920} fps={30} durationInFrames={SHOWCASE_FRAMES} />
);

registerRoot(ShowcaseRoot);
