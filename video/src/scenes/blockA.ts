import type React from "react";
import {S01_Hook} from "./S01_Hook";
import {S02_WhatItIs} from "./S02_WhatItIs";
import {S03_Command} from "./S03_Command";
import {S04_Drop} from "./S04_Drop";
import {S05_Flow} from "./S05_Flow";
import {S06_LoopCode} from "./S06_LoopCode";
import {S07_SelfCorrect} from "./S07_SelfCorrect";
import {S08_ThinkAhead} from "./S08_ThinkAhead";

export const blockA: Record<string, React.FC> = {
  S01_Hook,
  S02_WhatItIs,
  S03_Command,
  S04_Drop,
  S05_Flow,
  S06_LoopCode,
  S07_SelfCorrect,
  S08_ThinkAhead,
};
