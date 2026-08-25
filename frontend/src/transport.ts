import { claimTransport, registerTransport } from "./audioBus";

/** Continuous playback of a whole timeline.
 *
 *  Narration clips play in sequence and effect clips play **over** them at
 *  their own start times, which is the point: you hear what an editor would
 *  assemble, before assembling it. The per-item `Player` deliberately pauses
 *  its siblings; that is the opposite of what is needed here, so this owns its
 *  own elements and the two arbitrate through `audioBus`.
 *
 *  **Accuracy.** Position comes from `performance.now()`, not from any one
 *  element's `currentTime` — several are playing and none of them is the clock.
 *  Starting an `<audio>` element is accurate to a few tens of milliseconds, so
 *  a layered cue can sit slightly off its mark. Web Audio would be
 *  sample-accurate but only after fetching and decoding every take up front:
 *  tens of megabytes before a long episode could play at all. For a preview
 *  that is the wrong trade.
 */

export interface Clip {
  id: string;
  src: string;
  /** Seconds into the timeline. Straight from the timeline endpoint, which
   *  already includes the inter-chunk gaps the export inserts. */
  startAt: number;
  duration: number;
  lane: "narration" | "effect";
}

export interface TransportState {
  playing: boolean;
  position: number;
  duration: number;
  muted: Record<Clip["lane"], boolean>;
  ready: boolean;
}

type Listener = (state: TransportState) => void;

const EPSILON = 0.03;

/** Seams the tests drive.
 *
 *  The clock and the element constructor are injectable so the scheduling can
 *  be asserted without a browser or a real audio file. This is the most
 *  stateful code in the frontend; leaving it untestable would mean trusting it
 *  on the strength of a screenshot. */
export interface Deps {
  createAudio: (src: string) => HTMLAudioElement;
  now: () => number;
  schedule: (fn: () => void) => number;
  cancel: (handle: number) => void;
}

let deps: Deps = {
  createAudio: (src) => new Audio(src),
  now: () => performance.now(),
  schedule: (fn) => requestAnimationFrame(fn),
  cancel: (handle) => cancelAnimationFrame(handle),
};

export function configure(next: Partial<Deps>): void {
  deps = { ...deps, ...next };
}

/** Wipe all state. Tests call this between cases. */
export function reset(): void {
  stop();
  for (const audio of elements.values()) audio.pause();
  elements = new Map();
  clips = [];
  duration = 0;
  position = 0;
  muted.narration = false;
  muted.effect = false;
  listeners = new Set();
}

let clips: Clip[] = [];
let elements = new Map<string, HTMLAudioElement>();
let listeners = new Set<Listener>();

let playing = false;
let position = 0;
let duration = 0;
let lastTick = 0;
let frame: number | null = null;
const muted: Record<Clip["lane"], boolean> = { narration: false, effect: false };

function state(): TransportState {
  return { playing, position, duration, muted: { ...muted }, ready: clips.length > 0 };
}

function publish(): void {
  const snapshot = state();
  for (const listener of listeners) listener(snapshot);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  listener(state());
  return () => listeners.delete(listener);
}

/** Replace the loaded timeline. Stops playback if anything actually changed. */
export function load(next: Clip[]): void {
  const same =
    next.length === clips.length &&
    next.every((c, i) => c.id === clips[i].id && c.src === clips[i].src);
  if (same) return;

  stop();
  for (const element of elements.values()) {
    element.pause();
    element.src = "";
  }
  elements = new Map();
  clips = [...next].sort((a, b) => a.startAt - b.startAt);
  duration = clips.reduce((max, c) => Math.max(max, c.startAt + c.duration), 0);
  position = 0;
  publish();
}

function element(clip: Clip): HTMLAudioElement {
  let found = elements.get(clip.id);
  if (!found) {
    found = deps.createAudio(clip.src);
    found.preload = "auto";
    elements.set(clip.id, found);
  }
  return found;
}

/** Warm the next couple of clips so a boundary does not stutter. */
function preloadAround(at: number): void {
  const upcoming = clips.filter((c) => c.startAt >= at - EPSILON).slice(0, 3);
  for (const clip of upcoming) element(clip).load();
}

function isLive(clip: Clip, at: number): boolean {
  return at >= clip.startAt - EPSILON && at < clip.startAt + clip.duration;
}

function sync(at: number, seeking: boolean): void {
  for (const clip of clips) {
    const audio = element(clip);
    const live = isLive(clip, at) && !muted[clip.lane];

    if (!live) {
      if (!audio.paused) audio.pause();
      continue;
    }

    const offset = Math.max(0, at - clip.startAt);
    // Only correct the element's own clock when it has drifted far enough to
    // hear, or a seek has moved the playhead. Nudging it every frame would
    // stutter.
    if (seeking || Math.abs(audio.currentTime - offset) > 0.25) {
      try {
        audio.currentTime = offset;
      } catch {
        /* not seekable yet; it will catch up on the next tick */
      }
    }
    if (audio.paused) void audio.play().catch(() => undefined);
  }
}

function tick(): void {
  const now = deps.now();
  position += (now - lastTick) / 1000;
  lastTick = now;

  if (position >= duration) {
    position = duration;
    stop();
    // Rewind so pressing play again starts from the top rather than the end.
    position = 0;
    publish();
    return;
  }

  sync(position, false);
  publish();
  frame = deps.schedule(tick);
}

export function play(): void {
  if (playing || !clips.length) return;
  claimTransport();
  playing = true;
  lastTick = deps.now();
  preloadAround(position);
  sync(position, true);
  frame = deps.schedule(tick);
  publish();
}

export function stop(): void {
  playing = false;
  if (frame !== null) {
    deps.cancel(frame);
    frame = null;
  }
  for (const audio of elements.values()) if (!audio.paused) audio.pause();
  publish();
}

export function toggle(): void {
  if (playing) stop();
  else play();
}

export function seek(to: number): void {
  position = Math.min(Math.max(0, to), duration);
  preloadAround(position);
  if (playing) {
    lastTick = deps.now();
    sync(position, true);
  }
  publish();
}

export function setMuted(lane: Clip["lane"], value: boolean): void {
  muted[lane] = value;
  if (playing) sync(position, false);
  publish();
}

/** Which clips are audible right now — used by the tests and the track view. */
export function liveClips(at: number = position): Clip[] {
  return clips.filter((c) => isLive(c, at) && !muted[c.lane]);
}

export function loadedClips(): Clip[] {
  return [...clips];
}

/** For tests: the element backing a clip, so playback can be asserted. */
export function elementFor(id: string): HTMLAudioElement | undefined {
  return elements.get(id);
}

// The transport is a module singleton, so it registers once on import.
registerTransport(stop);
