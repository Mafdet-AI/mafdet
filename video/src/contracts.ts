// The shared contracts of the edit. Everyone builds on these; change them only through the orchestrator.

/** beats.json, written by tools/analyze_audio.py. Every time is in SECONDS FROM THE START OF THE CLIP
 * (the excerpt of the track the video uses, exported to public/audio/track.wav), never of the full track. */
export type Beats = {
  source: string;              // the track's file name
  bpm: number;
  fps: 30;
  clip: {start: number; end: number};   // where the excerpt sits in the full track, seconds
  duration: number;            // of the clip, seconds (30-45)
  durationInFrames: number;    // round(duration * fps)
  beats: number[];             // every beat
  downbeats: number[];         // first beat of each bar (a subset of beats)
  onsets: number[];            // percussive onsets (finer than beats), for stutters and glitches
  drop: number;                // the drop: the hardest downbeat, where section "drop" starts
  sections: {name: "hook" | "build" | "drop" | "outro"; start: number; end: number}[];   // contiguous, in order
  energy: number[];            // one value per video frame, 0..1 (smoothed RMS), length durationInFrames
};

/** The look: black, white, one accent. Nothing else, except the red/cyan of a glitch split. */
export const COLORS = {black: "#050505", white: "#F5F5F5", accent: "#E10600", dim: "#8A8A8A", line: "#2A2A2A"} as const;
export const SIZE = {width: 1080, height: 1920} as const;
/** Keep text inside this box: TikTok's interface covers the rest on a phone. */
export const SAFE = {top: 220, bottom: 420, side: 70} as const;

// ---- component props (src/components/<Name>.tsx, each a named export of the same name) ----

/** One word or a very short phrase slammed in: arrives oversized, lands in ~3 frames, optional RGB split
 * decaying after the hit. Upper-cased, Anton. Fits `maxWidth` by scaling down, never wraps. */
export type PunchTextProps = {
  text: string;
  at: number;                  // frame (relative to the enclosing Sequence) it hits
  size?: number;               // px, default 220
  color?: string;              // default COLORS.white
  maxWidth?: number;           // default SIZE.width - 2 * SAFE.side
  split?: number;              // px of RGB split at the hit, default 14
  style?: React.CSSProperties; // positioning by the scene
};

/** Wraps a scene: for `frames` frames from `at`, slices it in horizontal bands shifted sideways with an RGB
 * split (deterministic from `seed`). Used on cuts. */
export type GlitchTransitionProps = {at: number; frames?: number; seed?: number; intensity?: number; children: React.ReactNode};

/** A full-screen flash at `at`: 1-2 frames, then gone. */
export type FlashCutProps = {at: number; frames?: number; color?: string};

/** Wraps a scene: on every frame in `hits` (frames relative to the Sequence), a punch-in zoom and a shake
 * that decay over `decay` frames. Deterministic (remotion `random`), no Math.random. */
export type ShakeOnBeatProps = {hits: number[]; strength?: number; zoom?: number; decay?: number; children: React.ReactNode};

/** A terminal or code block that types itself with a block cursor, JetBrains Mono, thin 1px frame, sharp
 * corners. `lines[].color` may be "white" | "accent" | "dim". Typing runs from `at` over `frames`. */
export type CodeRevealProps = {
  lines: {text: string; color?: "white" | "accent" | "dim"}[];
  at: number;
  frames: number;
  title?: string;              // small label on the frame, e.g. "mafdet agent"
  fontSize?: number;           // default 34
  style?: React.CSSProperties;
};

/** The agent's flow: thin 2px boxes and lines, sharp corners, vertical. Node i appears at `appear[i]`
 * (frame), its edge from the previous node draws just before; the node whose index is `active(frame)` is
 * filled with the accent. Labels upper-case Anton, `note` in mono under it. */
export type FlowDiagramProps = {
  nodes: {label: string; note?: string}[];
  appear: number[];            // frame at which each node lands, same length as nodes
  loopBack?: {from: number; to: number; at: number};   // an edge going back up (the tool loop), drawn at `at`
  style?: React.CSSProperties;
};

/** Film on top of everything: animated grain, vignette, a faint chromatic fringe. */
export type GrainOverlayProps = {opacity?: number};
