// How each headline moves, decided in one place: the hierarchy of the edit.
//
//   slam     the hero: oversized, blurred, an RGB split, lands in 3 frames, then a slow push. Only for what
//            matters most: the name, the numbers, the key words. If everything slams, nothing does.
//   rise     a lead line: comes up from behind a mask, then drifts upward.
//   stagger  a lead line, letter by letter: each falls into place a frame after the one before.
//   type     typed on, a block cursor after the last letter: for things you type.
//   track    letters pulled together from wide spacing, then slowly spreading again: for calm statements.
//   slideL / slideR   support words in a list: whipped in from the side with motion trails, alternating,
//            and still moving the way they came.
export type Motion = "slam" | "rise" | "stagger" | "type" | "track" | "slideL" | "slideR";

export const MOTION: Record<string, Motion> = {
  // hero
  "MAFDET": "slam", "AGENT": "slam", "ANYWHERE?": "slam", "THINKING": "slam", "NOW WATCH": "slam", "ENTER": "slam",
  "UNDER 2 S": "slam", "2,359": "slam", "86 OF 86": "slam", "207 TOOLS": "slam", "~60": "slam", "Y": "slam",
  "AHEAD,": "slam", "TOGETHER.": "slam",
  // the hook: a question built line by line
  "YOUR": "rise", "BINARY": "rise", "MACHINE": "rise", "WHY": "rise", "SEND IT": "rise",
  // what it is
  "A LOCAL": "rise", "MODEL": "rise", "ON YOUR": "rise", "WHILE": "type", "YOU TYPE": "type", "WHILE YOU TYPE": "type",
  "READS": "slideL", "FILES": "slideR", "RUNS": "slideL", "COMMANDS": "slideR", "USES": "slideL", "MCP TOOLS": "slideR",
  "UNTIL": "rise", "IT'S DONE": "rise",
  "ONE COMMAND": "track", "THE CHEETAH": "stagger", "HUNTS AHEAD": "rise",
  // how it works
  "THE LOOP": "stagger", "TOOL CALLS": "rise", "NONE": "slideL", "MALFORMED": "slideR",
  "WATCH IT WORK": "stagger", "EDITS": "slideR", "ANSWERS": "slideL",
  "IT THINKS": "stagger",
  "WITHOUT NOTES": "track", "UP TO 5.5 S": "rise",
  "ASK": "stagger", "ALL": "stagger", "SHIFT+TAB": "type", "IT ASKS FIRST": "rise", "IT RUNS": "rise",
  "4,000 CHARS": "track", "4,496": "rise", "NOTES STAY": "rise",
  "16 START ON": "slideR", "1,696 TOKENS": "slideL", "FIND_TOOLS": "type", "FINDS THE REST": "slideR",
  "TURNS THEM ON": "slideL",
  "--WALK": "type", "EVERY FUNCTION": "slideL", "LEAVES UP": "rise", "ONE AT A TIME": "stagger", "0 FAILED": "rise",
};

export const motionOf = (label: string): Motion => MOTION[label] ?? "rise";
