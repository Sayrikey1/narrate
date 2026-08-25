import { afterEach, describe, expect, it, vi } from "vitest";
import * as bus from "../audioBus";
import * as transport from "../transport";
import type { Clip } from "../transport";

/** A stand-in for HTMLAudioElement: enough surface for the scheduler, and
 *  inspectable, so playback can be asserted without a browser or a real file. */
function fakeAudio(src: string) {
  return {
    src,
    paused: true,
    currentTime: 0,
    preload: "",
    load: vi.fn(),
    play: vi.fn(function (this: { paused: boolean }) {
      this.paused = false;
      return Promise.resolve();
    }),
    pause: vi.fn(function (this: { paused: boolean }) {
      this.paused = true;
    }),
  } as unknown as HTMLAudioElement;
}

/** A hand-cranked clock, so a test can advance time deterministically. */
function harness() {
  let now = 0;
  const pending: (() => void)[] = [];
  transport.configure({
    createAudio: fakeAudio,
    now: () => now,
    schedule: (fn) => {
      pending.push(fn);
      return pending.length;
    },
    cancel: () => undefined,
  });
  return {
    /** Advance the clock and run one scheduled frame. */
    tick(seconds: number) {
      now += seconds * 1000;
      const next = pending.shift();
      next?.();
    },
  };
}

/** Three narration clips with 0.4s gaps, and one effect over the second — the
 *  shape `build_timeline` produces. */
const CLIPS: Clip[] = [
  { id: "n1", src: "/a1", startAt: 0, duration: 5, lane: "narration" },
  { id: "n2", src: "/a2", startAt: 5.4, duration: 5, lane: "narration" },
  { id: "e1", src: "/e1", startAt: 5.4, duration: 3, lane: "effect" },
  { id: "n3", src: "/a3", startAt: 10.8, duration: 4, lane: "narration" },
];

afterEach(() => transport.reset());

describe("loading", () => {
  it("takes its duration from the furthest clip end", () => {
    harness();
    transport.load(CLIPS);
    // 10.8 + 4 — the gaps are already inside the start times.
    expect(transport.loadedClips().length).toBe(4);
    let seen = 0;
    transport.subscribe((s) => (seen = s.duration));
    expect(seen).toBeCloseTo(14.8);
  });

  it("keeps clip start times exactly as given", () => {
    harness();
    transport.load(CLIPS);
    expect(transport.loadedClips().map((c) => c.startAt)).toEqual([0, 5.4, 5.4, 10.8]);
  });
});

describe("continuous playback", () => {
  it("plays the first narration clip and nothing else", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(0.1);

    expect(transport.elementFor("n1")!.paused).toBe(false);
    expect(transport.elementFor("n2")!.paused).toBe(true);
    expect(transport.elementFor("n3")!.paused).toBe(true);
  });

  it("moves to the next clip as the clock crosses its start", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(6);

    expect(transport.elementFor("n1")!.paused).toBe(true);
    expect(transport.elementFor("n2")!.paused).toBe(false);
  });

  it("layers an effect over the narration it sits on", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(6);

    // Both audible at once — the whole point of the feature.
    expect(transport.elementFor("n2")!.paused).toBe(false);
    expect(transport.elementFor("e1")!.paused).toBe(false);
    expect(transport.liveClips().map((c) => c.id).sort()).toEqual(["e1", "n2"]);
  });

  it("drops the effect once its span ends, leaving the narration playing", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(6);
    clock.tick(3);

    expect(transport.elementFor("e1")!.paused).toBe(true);
    expect(transport.elementFor("n2")!.paused).toBe(false);
  });

  it("stops and rewinds at the end", () => {
    const clock = harness();
    transport.load(CLIPS);
    let state = { playing: false, position: -1 };
    transport.subscribe((s) => (state = { playing: s.playing, position: s.position }));

    transport.play();
    clock.tick(20);

    expect(state.playing).toBe(false);
    expect(state.position).toBe(0);
    for (const clip of CLIPS) expect(transport.elementFor(clip.id)!.paused).toBe(true);
  });
});

describe("seeking", () => {
  it("plays the clip containing the new position, from the right offset", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(0.1);

    transport.seek(12);

    expect(transport.elementFor("n3")!.paused).toBe(false);
    expect(transport.elementFor("n1")!.paused).toBe(true);
    // 12 − 10.8 into the third clip.
    expect(transport.elementFor("n3")!.currentTime).toBeCloseTo(1.2, 1);
  });

  it("clamps to the timeline", () => {
    harness();
    transport.load(CLIPS);
    let position = -1;
    transport.subscribe((s) => (position = s.position));

    transport.seek(-5);
    expect(position).toBe(0);
    transport.seek(999);
    expect(position).toBeCloseTo(14.8);
  });
});

describe("muting a lane", () => {
  it("silences the effects but not the narration", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(6);

    transport.setMuted("effect", true);

    expect(transport.elementFor("e1")!.paused).toBe(true);
    expect(transport.elementFor("n2")!.paused).toBe(false);
    expect(transport.liveClips().map((c) => c.id)).toEqual(["n2"]);
  });
});

describe("arbitration with a solo player", () => {
  it("a solo audition stops the transport", () => {
    const clock = harness();
    transport.load(CLIPS);
    transport.play();
    clock.tick(0.1);
    expect(transport.elementFor("n1")!.paused).toBe(false);

    const soloStop = vi.fn();
    bus.claimSolo(soloStop);

    expect(transport.elementFor("n1")!.paused).toBe(true);
  });

  it("starting the transport stops the solo", () => {
    harness();
    transport.load(CLIPS);
    const soloStop = vi.fn();
    bus.claimSolo(soloStop);

    transport.play();

    expect(soloStop).toHaveBeenCalled();
  });

  it("a second solo stops the first", () => {
    harness();
    const first = vi.fn();
    const second = vi.fn();
    bus.claimSolo(first);
    bus.claimSolo(second);
    expect(first).toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();
  });
});
