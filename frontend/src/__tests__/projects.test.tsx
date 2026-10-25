// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeTable } from "../components/EpisodeTable";
import { PickUp } from "../components/PickUp";
import { ProjectCards } from "../components/ProjectCards";
import { ProjectSettings } from "../components/ProjectSettings";
import type { ChunkRow, Cost, Model, Project, ScriptSummary, Take, Timeline } from "../types";

const updateProject = vi.fn(async () => ({ rejected_settings: [] }));
const costs: Record<number, Partial<Cost>> = {};

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: {
    voices: () => Promise.resolve([]),
    voicePreview: (id: string) => `/api/voices/${id}/preview`,
    updateProject: (...args: unknown[]) => updateProject(...(args as [])),
    cost: (id: number) => Promise.resolve(costs[id]),
  },
}));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  updateProject.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const render = async (node: React.ReactNode) => {
  await act(async () => root.render(node));
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

function model(model_id: string, over: Partial<Model> = {}): Model {
  return {
    model_id,
    label: model_id,
    usd_per_1k: 0.1,
    max_chars: 5000,
    request_stitching: false,
    continuity_mode: "none",
    audio_tags: true,
    settings_honoured: ["stability"],
    dialogue: false,
    dialogue_max_chars: 2000,
    long_form: "expressive",
    default: false,
    note: "",
    ...over,
  };
}

function project(id: number, name: string, model_id: string, voice_id: string, over: Partial<Project> = {}): Project {
  return {
    id,
    name,
    model_id,
    voice_id,
    spend_micros: 0,
    waste_pct: 0,
    month_micros: 0,
    cap_micros: null,
    cap_used_pct: 0,
    ...over,
  };
}

const MODELS = [model("eleven_multilingual_v2", { long_form: "best" }), model("eleven_v3", { default: true })];
const PROJECTS = [
  project(1, "Old", "eleven_multilingual_v2", "voice-1"),
  project(2, "New", "eleven_v3", "voice-2"),
];

const submit = async () => {
  const form = host.querySelector("form") as HTMLFormElement;
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
};

describe("the project settings", () => {
  it("edit the project now selected, not the one first shown", async () => {
    await render(<ProjectSettings project={PROJECTS[0]!} models={MODELS} onChanged={() => {}} />);
    // Another project is selected while the settings are open.
    await render(<ProjectSettings project={PROJECTS[1]!} models={MODELS} onChanged={() => {}} />);
    await submit();

    expect(updateProject).toHaveBeenCalledWith(
      2,
      expect.objectContaining({ model_id: "eleven_v3", voice_id: "voice-2" }),
    );
  });

  it("warn that a new model bills generated chunks again", async () => {
    await render(<ProjectSettings project={PROJECTS[0]!} models={MODELS} onChanged={() => {}} />);
    expect(host.textContent).not.toContain("billed again");

    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "eleven_v3";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(host.textContent).toContain("billed again");
  });

  it("send an emptied cap as zero, which removes it", async () => {
    const capped = project(3, "Capped", "eleven_v3", "voice-3", { cap_micros: 15_000_000 });
    await render(<ProjectSettings project={capped} models={MODELS} onChanged={() => {}} />);

    const cap = host.querySelector<HTMLInputElement>('input[type="number"]')!;
    expect(cap.value).toBe("15");
    await act(async () => {
      // React tracks the value it last set; going through the native setter is
      // what makes the change event count as an edit.
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(cap, "");
      cap.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await submit();

    expect(updateProject).toHaveBeenCalledWith(3, expect.objectContaining({ monthly_cap_usd: 0 }));
  });
});

describe("the project cards", () => {
  const scripts: ScriptSummary[] = [
    { id: 10, project_id: 1, title: "001", chunks: 3, chars: 3000, model_id: null },
    { id: 11, project_id: 1, title: "002", chunks: 4, chars: 4000, model_id: null },
  ];

  it("press the selected project, warn near a cap, and pick on click", async () => {
    const near = project(2, "Near", "eleven_v3", "voice-2", {
      cap_micros: 1_000_000,
      month_micros: 840_000,
      cap_used_pct: 84,
    });
    const onSelect = vi.fn();
    await render(
      <ProjectCards
        projects={[PROJECTS[0]!, near]}
        scripts={scripts}
        voices={[]}
        selected={1}
        onSelect={onSelect}
      />,
    );

    const cards = host.querySelectorAll<HTMLButtonElement>("button.project-card");
    expect(cards[0]!.getAttribute("aria-pressed")).toBe("true");
    expect(cards[0]!.textContent).toContain("✓ Selected");
    expect(cards[0]!.textContent).toContain("2 episodes");
    expect(cards[1]!.textContent).toContain("Near cap");
    expect(cards[1]!.textContent).toContain("$0.84 / $1 · 84%");

    await act(async () => cards[1]!.click());
    expect(onSelect).toHaveBeenCalledWith(2);
  });
});

describe("the episode table", () => {
  it("says what each episode has left to generate", async () => {
    const outstanding = (chunks: number, cost_micros: number) => ({
      chunks,
      chars: chunks * 1000,
      effects: 0,
      effect_seconds: 0,
      cost_micros,
    });
    costs[20] = { cost_micros: 0, outstanding: outstanding(3, 300_000) };
    costs[21] = { cost_micros: 900_000, outstanding: outstanding(1, 100_000) };
    costs[22] = { cost_micros: 1_200_000, outstanding: outstanding(0, 0) };
    const episodes: ScriptSummary[] = [20, 21, 22].map((id) => ({
      id,
      project_id: 1,
      title: `0${id}`,
      chunks: 3,
      chars: 3000,
      model_id: null,
    }));

    await render(<EpisodeTable projectName="HINDSIGHT" episodes={episodes} />);
    const rows = [...host.querySelectorAll("tbody tr")].map((r) => r.textContent);

    expect(rows[0]).toContain("Script only — about $0.30 to generate");
    expect(rows[1]).toContain("1 of 3 chunks to generate — about $0.10");
    expect(rows[1]).toContain("$0.9000");
    expect(rows[2]).toContain("Every chunk has audio");
  });
});

describe("picking up where you left off", () => {
  function take(over: Partial<Take>): Take {
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
  const chunk = (ordinal: number, takes: Take[]): ChunkRow => ({
    ordinal,
    text: "Words.",
    chars: 1000,
    source: "paragraph",
    target_start_s: null,
    takes,
  });
  const episode: ScriptSummary = { id: 17, project_id: 1, title: "011", chunks: 3, chars: 3000, model_id: null };

  it("names what is flagged and what has no audio, and links to the fix", async () => {
    const chunks = [
      chunk(1, [take({ id: 1, verify_status: "suspect" })]),
      // Heard and said to be fine: not flagged any more.
      chunk(2, [take({ id: 2, verify_status: "suspect", verify_accepted: true })]),
      chunk(3, []),
    ];
    const timeline = {
      script_id: 17,
      title: "011",
      project: "HINDSIGHT",
      runtime_s: 130,
      complete: false,
      entries: [1, 2, 3].map((n) => ({
        index: n,
        kind: "narration" as const,
        start: "",
        end: "",
        start_s: 0,
        end_s: 0,
        duration_s: n < 3 ? 65 : 0,
        label: "",
        chunk_ordinal: n,
        take_id: n < 3 ? n : null,
        slot_id: null,
        effect_id: null,
        generated: n < 3,
        target_s: null,
        drift_s: null,
      })),
    } satisfies Timeline;

    await render(<PickUp episode={episode} timeline={timeline} chunks={chunks} />);

    expect(host.textContent).toContain("2:10 so far");
    expect(host.textContent).toContain("1 take flagged");
    expect(host.textContent).toContain("Chunk 3 has no audio, so export is waiting on it");
    const fix = [...host.querySelectorAll("a")].find((a) => a.textContent?.includes("Fix what's flagged"));
    expect(fix?.getAttribute("href")).toBe("/script/17#fix");
    // Nothing loaded into the player, so nothing to play from.
    expect(button("Play from")).toBeUndefined();
  });
});
