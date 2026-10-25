import { bytes } from "./api";
import type { MediaFile, ProjectMedia, Slot, Timeline } from "./types";

/** What a file in the media library is, as somebody looking for it thinks of
 *  it — rather than which folder it happens to be in. */
export type FileKind = "master" | "piece" | "document" | "effect" | "take";

export interface Entry {
  file: MediaFile;
  kind: FileKind;
  /** The episode it belongs to; `null` for the project's effect library. */
  scriptId: number | null;
  icon: string;
  /** What it is, in words: "master with effects mixed in", "chunk 3 · take 2". */
  meta: string;
  /** Drawn on the effect colour: an effect, or a master with effects in it. */
  fx: boolean;
  /** Takes only. */
  chunk?: number;
  take?: number;
  inCut?: boolean;
}

/** `HINDSIGHT_00-01-38-320_00-04-03-520.mp3`: a piece of the timeline, named
 *  by where it starts and ends so it can be dropped straight onto a track. */
const PIECE = /_(\d{2})-(\d{2})-(\d{2})-(\d{3})_(\d{2})-(\d{2})-(\d{2})-(\d{3})\.\w+$/;

/** Documents that can be read in place rather than downloaded first. */
export const READABLE = /\.(md|markdown|txt|text|json)$/i;

const DOCUMENTS: Record<string, [string, string]> = {
  "plan.md": ["🗺️", "editing plan — what plays when"],
  "publish.md": ["🚀", "chapters, title, description, tags"],
  "publish.json": ["🧾", "the publish pack, for scripts"],
  "timeline.json": ["🧭", "the timeline, for scripts"],
};

/** Every file in the listing as an entry, described with what the episode's
 *  own timeline knows about it where that is to hand. */
export function entriesOf(media: ProjectMedia, timeline: Timeline | null): Entry[] {
  const entries: Entry[] = [];
  for (const group of media.groups) {
    for (const file of group.files) {
      if (group.kind === "take") entries.push(takeEntry(file));
      else if (group.kind === "effect") entries.push(effectEntry(file));
      else entries.push(exportEntry(file, timeline?.script_id === file.script_id ? timeline : null));
    }
  }
  return entries;
}

function exportEntry(file: MediaFile, timeline: Timeline | null): Entry {
  const base = { file, scriptId: file.script_id };
  const piece = file.name.match(PIECE);
  if (piece) {
    const n = piece.slice(1).map(Number);
    const start = n[0]! * 3600 + n[1]! * 60 + n[2]! + n[3]! / 1000;
    const end = n[4]! * 3600 + n[5]! * 60 + n[6]! + n[7]! / 1000;
    // The timeline as it is now: an export made before a retake changed the
    // timings matches nothing, and says only where the piece sits.
    const entry = timeline?.entries.find(
      (e) => Math.abs(e.start_s - start) < 0.002 && Math.abs(e.end_s - end) < 0.002,
    );
    const effect = entry ? entry.kind !== "narration" : false;
    const what = entry
      ? entry.kind === "narration"
        ? `chunk ${entry.chunk_ordinal}`
        : `effect · ${entry.label}`
      : null;
    return {
      ...base,
      kind: "piece",
      icon: effect ? "✨" : "🎙️",
      fx: effect,
      meta: [what, `${clock(start)} – ${clock(end)}`].filter(Boolean).join(" · "),
    };
  }
  if (file.playable) {
    const fx = /_fx\.\w+$/.test(file.name);
    return {
      ...base,
      kind: "master",
      icon: fx ? "✨" : "🎧",
      fx,
      meta: [fx ? "master with effects mixed in" : "master", day(file.modified)].filter(Boolean).join(" · "),
    };
  }
  const known = DOCUMENTS[file.name];
  const suffix = file.name.split(".").pop()?.toLowerCase() ?? "";
  return {
    ...base,
    kind: "document",
    icon: known?.[0] ?? (suffix === "pdf" ? "📕" : "📝"),
    fx: false,
    meta: known?.[1] ?? (suffix === "pdf" ? "PDF" : suffix === "md" ? "the script, as written" : `${suffix.toUpperCase()} file`),
  };
}

function takeEntry(file: MediaFile): Entry {
  // "chunk 3 · take 2 · in the cut", as the server labels it.
  const found = file.label.match(/chunk (\d+) · take (\d+)/);
  return {
    file,
    kind: "take",
    scriptId: file.script_id,
    icon: "🎙️",
    fx: false,
    chunk: found ? Number(found[1]) : undefined,
    take: found ? Number(found[2]) : undefined,
    inCut: file.label.includes("in the cut"),
    meta: [
      file.duration_s ? clock(file.duration_s) : null,
      bytes(file.bytes),
      day(file.modified),
    ]
      .filter(Boolean)
      .join(" · "),
  };
}

function effectEntry(file: MediaFile): Entry {
  return {
    file,
    kind: "effect",
    scriptId: null,
    icon: "✨",
    fx: true,
    // "camera-flash · 2 placements", less the name the file already shows.
    meta: [file.label.split(" · ").slice(1).join(" · "), file.duration_s ? `${file.duration_s.toFixed(1)}s` : null]
      .filter(Boolean)
      .join(" · "),
  };
}

/** Which files a scope shows. An episode's own: its export, its takes, and the
 *  library effects it places — the library itself has no episode. */
export function inScope(entry: Entry, scriptId: number | null, slots: Slot[]): boolean {
  if (scriptId === null) return true;
  if (entry.kind === "effect") return slots.some((s) => s.effect_id === entry.file.effect_id);
  return entry.scriptId === scriptId;
}

export type Sort = "timeline" | "newest" | "largest" | "name";

export function sorted(entries: Entry[], sort: Sort): Entry[] {
  if (sort === "timeline") return entries;
  const copy = [...entries];
  if (sort === "newest") copy.sort((a, b) => b.file.modified - a.file.modified);
  if (sort === "largest") copy.sort((a, b) => b.file.bytes - a.file.bytes);
  if (sort === "name") copy.sort((a, b) => a.file.name.localeCompare(b.file.name, undefined, { numeric: true }));
  return copy;
}

export function matches(entry: Entry, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return (
    entry.file.name.toLowerCase().includes(needle) ||
    entry.meta.toLowerCase().includes(needle) ||
    entry.file.label.toLowerCase().includes(needle)
  );
}

/** "21 Oct". */
export function day(unix: number, weekday = false): string {
  if (!unix) return "";
  return new Date(unix * 1000).toLocaleDateString("en-GB", {
    ...(weekday ? { weekday: "short" as const } : {}),
    day: "numeric",
    month: "short",
  });
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = String(whole % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}
