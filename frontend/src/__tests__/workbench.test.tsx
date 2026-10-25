// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExportCard } from "../components/ExportCard";
import { GeneratePanel } from "../components/GeneratePanel";
import { Stepper } from "../components/Stepper";
import { Menu } from "../ui/Menu";
import type { ChunkRow, Slot, Take, Timeline, TimelineEntry } from "../types";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const render = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(node);
  });
};

const click = async (element: Element | null | undefined) => {
  await act(async () => {
    (element as HTMLElement).click();
  });
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

function take(over: Partial<Take> = {}): Take {
  return {
    id: 1,
    ordinal: 1,
    status: "succeeded",
    billed_chars: 100,
    cost_micros: 10_000,
    duration_s: 60,
    model_id: "eleven_v3",
    in_cut: true,
    verify_status: "clear",
    verify_accepted: false,
    findings: [],
    ...over,
  };
}

function chunk(ordinal: number, takes: Take[]): ChunkRow {
  return { ordinal, text: `Chunk ${ordinal} words.`, chars: 1000, source: "paragraph", target_start_s: null, takes };
}

function entry(ordinal: number, generated: boolean): TimelineEntry {
  return {
    index: ordinal,
    kind: "narration",
    start: "00:00:00.000",
    end: "00:01:00.000",
    start_s: (ordinal - 1) * 60,
    end_s: ordinal * 60,
    duration_s: generated ? 60 : 0,
    label: `Chunk ${ordinal}`,
    chunk_ordinal: ordinal,
    take_id: generated ? ordinal : null,
    slot_id: null,
    effect_id: null,
    generated,
    target_s: null,
    drift_s: null,
  };
}

/** Seven chunks with audio and an eighth that never generated. */
function episode() {
  const chunks = [
    ...Array.from({ length: 7 }, (_, i) => chunk(i + 1, [take({ id: i + 1 })])),
    chunk(8, [take({ id: 99, status: "failed", in_cut: false, verify_status: "unverified" })]),
  ];
  const timeline: Timeline = {
    script_id: 17,
    title: "011",
    project: "HINDSIGHT",
    runtime_s: 420,
    complete: false,
    entries: [...Array.from({ length: 7 }, (_, i) => entry(i + 1, true)), entry(8, false)],
  };
  return { chunks, timeline };
}

describe("the stepper says what is in the way", () => {
  it("names the chunk that did not generate, and the export that waits on it", async () => {
    const { chunks, timeline } = episode();
    await render(<Stepper chunks={chunks} timeline={timeline} exported={null} publish={null} />);

    expect(host.textContent).toContain("7/8");
    expect(host.textContent).toContain("Chunk 8 didn't generate");
    expect(host.textContent).toContain("Waiting on chunk 8");
    expect(host.textContent).toContain("blocked");
  });
});

describe("export explains itself", () => {
  const props = () => {
    const { chunks, timeline } = episode();
    return {
      scriptId: 17,
      timeline,
      chunks,
      formats: [
        { key: "wav", label: "WAV", suffix: ".wav", codec: "pcm", lossless: true, note: "" },
        { key: "mp3", label: "MP3", suffix: ".mp3", codec: "mp3", lossless: false, note: "" },
      ],
      chosen: ["wav", "mp3"],
      onChoose: vi.fn(),
      mix: true,
      onMix: vi.fn(),
      busy: false,
      exported: null,
      onExport: vi.fn(),
      onGenerate: vi.fn(),
    };
  };

  it("says under a blocked button what will unlock it, and offers the fix", async () => {
    const p = props();
    await render(<ExportCard {...p} />);

    const go = button("Export 2 formats") as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    expect(go.getAttribute("aria-describedby")).toBe("export-why");
    expect(host.querySelector("#export-why")?.textContent).toBe(
      "Unlocks as soon as chunk 8 has audio.",
    );

    await click(button("Generate it"));
    expect(p.onGenerate).toHaveBeenCalled();
  });

  it("exports once every chunk has audio", async () => {
    const p = props();
    const complete = { ...p.timeline, complete: true, entries: p.timeline.entries.map((e) => ({ ...e, generated: true, duration_s: 60 })) };
    await render(<ExportCard {...p} timeline={complete} />);

    const go = button("Export 2 formats") as HTMLButtonElement;
    expect(go.disabled).toBe(false);
    await click(go);
    expect(p.onExport).toHaveBeenCalled();
  });

  it("asks for a format rather than exporting none", async () => {
    const p = props();
    const complete = { ...p.timeline, entries: p.timeline.entries.map((e) => ({ ...e, generated: true })) };
    await render(<ExportCard {...p} timeline={complete} chosen={[]} />);
    expect(host.textContent).toContain("Choose at least one format.");
  });
});

/** EventSource for a dry run: finishes when the test says so. */
class FakeRun {
  static last: FakeRun | null = null;
  private done?: (event: { data: string }) => void;
  onmessage: unknown = null;
  onerror: unknown = null;
  constructor(public url: string) {
    FakeRun.last = this;
  }
  addEventListener(type: string, listener: (event: { data: string }) => void) {
    if (type === "done") this.done = listener;
  }
  close() {}
  finish() {
    this.done?.({ data: JSON.stringify({ dry_run: true }) });
  }
}

describe("generate shows the price first", () => {
  const planned: Slot[] = [
    {
      id: 1,
      at_chunk_ordinal: 5,
      description: "camera flashes",
      duration_s: 4,
      loop: false,
      source: "manual",
      accepted: true,
      note: "",
      effect_id: null,
      slug: null,
      cost_micros: 0,
    },
  ];

  function stubDryRun() {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          run_key: "script-17",
          streaming: true,
          projection: {
            speech_micros: 114_000,
            effect_micros: 25_000,
            total_micros: 139_000,
            chunks: 1,
            chars: 1140,
            effects: 1,
            effect_seconds: 4,
          },
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("EventSource", FakeRun);
    return fetch;
  }

  it("prices with a dry run, and only offers to spend once it has finished", async () => {
    const fetch = stubDryRun();
    const onGenerate = vi.fn();
    await render(
      <GeneratePanel
        scriptId={17}
        slots={planned}
        missing={[8]}
        cost={null}
        busy={false}
        running={false}
        log={[]}
        onGenerate={onGenerate}
        onClose={() => {}}
      />,
    );

    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({ dry_run: true, with_effects: true });
    expect(host.textContent).toContain("Chunk 8");
    // The dry run is still a run on the server: spending now would be refused.
    expect((button("Pricing…") as HTMLButtonElement).disabled).toBe(true);

    await act(async () => FakeRun.last?.finish());
    // Two places on the button, as a price reads; the limit sent is exact.
    await click(button("Generate for $0.14"));

    expect(onGenerate).toHaveBeenCalledWith(true, 0.139);
  });

  it("prices again without the effects when they are left out", async () => {
    const fetch = stubDryRun();
    await render(
      <GeneratePanel
        scriptId={17}
        slots={planned}
        missing={[8]}
        cost={null}
        busy={false}
        running={false}
        log={[]}
        onGenerate={vi.fn()}
        onClose={() => {}}
      />,
    );
    const include = [...host.querySelectorAll("label")].find((l) =>
      l.textContent?.includes("Include the 1 planned effect"),
    );
    await click(include?.querySelector("input"));

    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toMatchObject({ with_effects: false });
  });
});

describe("the switcher is a menu a keyboard can drive", () => {
  const items = (onSelect: (key: number) => void) =>
    [1, 2, 3].map((n) => ({ key: n, label: `Episode 00${n}`, current: n === 2, onSelect: () => onSelect(n) }));

  it("opens on the current choice, and a choice closes it", async () => {
    const picked = vi.fn();
    await render(<Menu label="Switch episode" trigger="002" items={items(picked)} />);

    await click(button("002"));
    const choices = host.querySelectorAll('[role="menuitemradio"]');
    expect(choices.length).toBe(3);
    expect(document.activeElement?.textContent).toContain("Episode 002");
    expect(choices[1]?.getAttribute("aria-checked")).toBe("true");

    await click(choices[2]);
    expect(picked).toHaveBeenCalledWith(3);
    expect(host.querySelector('[role="menu"]')).toBeNull();
  });

  it("closes on Escape and hands focus back to its button", async () => {
    await render(<Menu label="Switch episode" trigger="002" items={items(vi.fn())} />);
    const trigger = button("002") as HTMLButtonElement;
    await click(trigger);

    await act(async () => {
      document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });

    expect(host.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});
