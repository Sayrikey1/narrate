import type { Finding } from "./types";

/** A word the transcriber mangled that the script writes with a capital
 *  inside a sentence: a name, which it mishears far more often than the
 *  narrator mispronounces. Worth a listen before paying to regenerate. */
export function isName(finding: Finding, text: string): boolean {
  if (finding.kind !== "garbled" && finding.kind !== "reworded") return false;
  const word = finding.expected?.trim();
  if (!word || word.includes(" ")) return false;
  const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`[a-z,;:]\\s+${escaped[0]!.toUpperCase()}${escaped.slice(1)}\\b`).test(text);
}

/** A chunk's words as they are heard: without the audio tags — `[serious]`,
 *  `[whispers]` — that direct the delivery on models that read them. */
export function spoken(text: string): string {
  return text.replace(/\[[^\]]*\]\s*/g, "").trim();
}
