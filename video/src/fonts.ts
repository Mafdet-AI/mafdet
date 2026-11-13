import {loadFont as loadAnton} from "@remotion/google-fonts/Anton";
import {loadFont as loadMono} from "@remotion/google-fonts/JetBrainsMono";

export const DISPLAY = loadAnton().fontFamily;
export const MONO = loadMono("normal", {weights: ["500", "800"], subsets: ["latin"]}).fontFamily;
