/** Seconds as `m:ss`, or `h:mm:ss` past an hour. Shared so the ruler, the
 *  players and the detail rows never disagree about how long something is. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  const s = String(whole % 60).padStart(2, "0");
  const m = Math.floor(whole / 60) % 60;
  const h = Math.floor(whole / 3600);
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** A timecode cell back to seconds, or `null` if it is not one.
 *
 *  Two shapes, told apart by how many parts there are rather than by guessing:
 *  `h:mm:ss` from the plan (which always writes all three), and `m:ss` from a
 *  chapter stamp (which drops the hour below an hour, because YouTube will not
 *  accept `00:04:31` where it accepts `4:31`). Shared so a clickable row seeks
 *  the same way on either page. */
export function parseStamp(text: string): number | null {
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$/.exec(text.trim());
  if (!match) return null;
  const [, first, second, third] = match;
  if (third === undefined) return Number(first) * 60 + Number(second);
  return Number(first) * 3600 + Number(second) * 60 + Number(third);
}
