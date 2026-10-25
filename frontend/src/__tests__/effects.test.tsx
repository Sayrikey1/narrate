// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EffectLibrary, FxCard, WhereTheySit } from "../components/EffectPanel";
import { EffectsPage } from "../pages/Effects";
import type { ChunkRow, Slot, Timeline } from "../types";

const deleteSlot = vi.fn(async () => ({}));
const updateSlot = vi.fn(async () => ({ id: 1, planned: true }));
const addSlot = vi.fn(async () => ({ id: 5 }));
const draft = vi.fn(async () => ({
  kind: "effects",
  stored: 2,
  cost_micros: 410,
  prompt_tokens: 800,
  completion_tokens: 90,
  model: "m",
  errors: [],
  description_written: false,
}));
const acceptSlot = vi.fn(async () => ({}));
const generateEffects = vi.fn(async (_id: number, dryRun: boolean) => ({
  outcomes: [{ status: dryRun ? "would_generate" : "generated", description: "camera flashes" }],
  cost_micros: 25_000,
}));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: {
    peaks: () => Promise.resolve({ peaks: [] }),
    draftQuote: () =>
      Promise.resolve({ kind: "effects", requests: 3, input_tokens: 900, output_tokens: 660, micros: 500, model: "m", available: true }),
    draft: (...args: unknown[]) => draft(...(args as [])),
    deleteSlot: (...args: unknown[]) => deleteSlot(...(args as [])),
    updateSlot: (...args: unknown[]) => updateSlot(...(args as [])),
    acceptSlot: (...args: unknown[]) => acceptSlot(...(args as [])),
    generateEffects: (id: number, dryRun: boolean) => generateEffects(id, dryRun),
    addSlot: (...args: unknown[]) => addSlot(...(args as [])),
    effectLibrary: () =>
      Promise.resolve([
        { id: 9, slug: "door-closing-3s", description: "door closing", duration_s: 3, loop: false, cost_micros: 25_000, placements: 2, episodes: 1 },
      ]),
    audioEffect: (id: number) => `/api/audio/effect/${id}`,
  },
}));

const seek = vi.fn();
const play = vi.fn();
vi.mock("../transport", () => ({
  seek: (at: number) => seek(at),
  play: () => play(),
}));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  for (const mock of [deleteSlot, updateSlot, addSlot, draft, acceptSlot, generateEffects, seek, play]) mock.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const render = async (node: React.ReactNode) => {
  await act(async () => root.render(node));
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find(
    (b) => b.textContent?.includes(label) || b.getAttribute("aria-label")?.includes(label),
  );

const click = async (element: Element | undefined) => {
  await act(async () => (element as HTMLElement).click());
};

const CHUNKS: ChunkRow[] = [1, 2, 3].map((ordinal) => ({
  ordinal,
  text: `Chunk ${ordinal} opens with these words.`,
  chars: 30,
  source: "paragraph",
  target_start_s: null,
  takes: [],
}));

function slot(over: Partial<Slot> = {}): Slot {
  return {
    id: 1,
    at_chunk_ordinal: 2,
    description: "door closing",
    duration_s: 3,
    loop: false,
    source: "marker",
    accepted: true,
    note: "",
    effect_id: 9,
    slug: "door-closing-3s",
    cost_micros: 25_000,
    ...over,
  };
}

describe("a placement", () => {
  it("asks before it is removed, and says the sound stays", async () => {
    const onChange = vi.fn();
    await render(<FxCard slot={slot()} reused={false} others={1} timeline={null} chunk={undefined} chunks={CHUNKS} onChange={onChange} />);

    await click(button("Remove door closing"));
    expect(host.textContent).toContain("Remove this placement?");
    expect(host.textContent).toContain("keeps its other placement");

    await click(button("Keep it"));
    expect(host.querySelector('[role="alertdialog"]')).toBeNull();
    expect(deleteSlot).not.toHaveBeenCalled();

    await click(button("Remove door closing"));
    const confirm = [...host.querySelectorAll('[role="alertdialog"] button')].find((b) => b.textContent === "Remove");
    await click(confirm);
    expect(deleteSlot).toHaveBeenCalledWith(1);
    expect(onChange).toHaveBeenCalled();
  });

  it("offers a suggestion to accept, for free, or to dismiss", async () => {
    const suggested = slot({ accepted: false, effect_id: null, slug: null, source: "suggested", note: "The passage turns to the verdict." });
    await render(<FxCard slot={suggested} reused={false} others={0} timeline={null} chunk={undefined} chunks={CHUNKS} onChange={vi.fn()} />);

    expect(host.textContent).toContain("not accepted yet");
    expect(host.textContent).toContain("Accepting is free");
    await click(button("Accept"));
    expect(acceptSlot).toHaveBeenCalledWith(1, true);
  });
});

describe("editing a placement", () => {
  it("moves it without touching its sound", async () => {
    await render(<FxCard slot={slot()} reused={false} others={0} timeline={null} chunk={undefined} chunks={CHUNKS} onChange={vi.fn()} />);
    await click(button("Edit door closing"));
    expect(host.textContent).toContain("Moving it keeps its sound, and is free.");

    const where = host.querySelector<HTMLSelectElement>(".fx-edit select")!;
    await act(async () => {
      where.value = "3";
      where.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => host.querySelector<HTMLFormElement>(".fx-edit")!.requestSubmit());

    expect(updateSlot).toHaveBeenCalledWith(1, { at_chunk_ordinal: 3 });
  });

  it("says a new description is a new sound, priced at the next Generate", async () => {
    await render(<FxCard slot={slot()} reused={false} others={0} timeline={null} chunk={undefined} chunks={CHUNKS} onChange={vi.fn()} />);
    await click(button("Edit door closing"));
    const description = host.querySelector<HTMLInputElement>(".fx-edit input")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(description, "door slamming");
      description.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(host.textContent).toContain("That's a different sound");
    await act(async () => host.querySelector<HTMLFormElement>(".fx-edit")!.requestSubmit());
    expect(updateSlot).toHaveBeenCalledWith(1, {
      at_chunk_ordinal: 2,
      description: "door slamming",
      duration_s: 3,
      loop: false,
    });
  });
});

describe("the effect library", () => {
  it("places a sound the project already paid for, free", async () => {
    const onChange = vi.fn();
    await render(
      <EffectLibrary projectId={1} projectName="HINDSIGHT" scriptId={17} chunks={CHUNKS} refreshKey={0} onChange={onChange} />,
    );
    expect(host.textContent).toContain("2 placements · 1 episode");

    await click(button("Place…"));
    await click(button("Place here — free"));
    expect(addSlot).toHaveBeenCalledWith(17, { effect_id: 9, at_chunk_ordinal: 1 });
    expect(onChange).toHaveBeenCalled();
  });
});

describe("generating effects", () => {
  it("prices with a dry run first, and spends only when asked", async () => {
    const planned = slot({ effect_id: null, slug: null });
    await render(
      <EffectsPage scriptId={17} script={null} slots={[planned]} chunks={[]} timeline={null} onRefresh={vi.fn()} />,
    );

    expect(generateEffects).toHaveBeenCalledWith(17, true);
    expect(generateEffects).toHaveBeenCalledTimes(1);

    await click(button("Generate 1 for $0.0250"));
    expect(generateEffects).toHaveBeenLastCalledWith(17, false);
    expect(host.textContent).toContain("Generated 1");
  });
});

describe("suggesting effects", () => {
  it("shows the price first, and what it cost after", async () => {
    const onRefresh = vi.fn();
    await render(
      <EffectsPage scriptId={17} script={null} slots={[]} chunks={[]} timeline={null} onRefresh={onRefresh} />,
    );
    const suggest = button("Suggest effects…")!;
    expect(suggest.textContent).toContain("~$0.0005");

    await click(suggest);
    expect(draft).toHaveBeenCalledWith(17, "effects");
    expect(host.textContent).toContain("2 suggestions added, none chosen · cost $0.0004");
    expect(onRefresh).toHaveBeenCalled();
  });
});

describe("where they sit", () => {
  it("plays the episode from an effect's place", async () => {
    const timeline = {
      script_id: 17,
      title: "011",
      project: "HINDSIGHT",
      runtime_s: 120,
      complete: true,
      entries: [
        { index: 1, kind: "narration" as const, start: "", end: "", start_s: 0, end_s: 60, duration_s: 60, label: "", chunk_ordinal: 1, take_id: 1, slot_id: null, effect_id: null, generated: true, target_s: null, drift_s: null },
        { index: 2, kind: "narration" as const, start: "", end: "", start_s: 60.4, end_s: 120, duration_s: 59.6, label: "", chunk_ordinal: 2, take_id: 2, slot_id: null, effect_id: null, generated: true, target_s: null, drift_s: null },
        { index: 3, kind: "effect" as const, start: "", end: "", start_s: 60.4, end_s: 63.4, duration_s: 3, label: "door closing", chunk_ordinal: null, take_id: null, slot_id: 1, effect_id: 9, generated: true, target_s: null, drift_s: null },
      ],
    } satisfies Timeline;
    await render(<WhereTheySit timeline={timeline} />);

    await click(button("door closing at 1:00"));
    expect(seek).toHaveBeenCalledWith(60.4);
    expect(play).toHaveBeenCalled();
  });
});
