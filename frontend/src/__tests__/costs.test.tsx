// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CostsPage } from "../pages/Costs";
import type { Cost, Project, ScriptSummary } from "../types";

function cost(cost_micros: number, over: Partial<Cost> = {}): Cost {
  return {
    operations: 1,
    takes: 1,
    billed_chars: 1000,
    cost_micros,
    credits: 1000,
    selected_micros: cost_micros,
    wasted_micros: 0,
    waste_pct: 0,
    display: "",
    by_kind: cost_micros
      ? [{ kind: "generation", provider: "elevenlabs", unit_kind: "characters", operations: 1, units: 1000, units_display: "1,000 chars", cost_micros, credits: 1000 }]
      : [],
    cost_per_minute_micros: cost_micros ? 50_000 : null,
    ...over,
  };
}

const COSTS: Record<number, Cost> = { 10: cost(100_000), 11: cost(0), 20: cost(300_000) };

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: {
    cost: (id: number) => Promise.resolve(COSTS[id]),
    deleted: () => Promise.resolve({ projects: [], scripts: [] }),
  },
}));

function project(id: number, name: string, over: Partial<Project> = {}): Project {
  return { id, name, voice_id: "v", model_id: "eleven_v3", spend_micros: 0, waste_pct: 0, month_micros: 0, cap_micros: null, cap_used_pct: 0, ...over };
}

const PROJECTS = [
  project(1, "Ep14", { spend_micros: 300_000 }),
  project(2, "HINDSIGHT", { spend_micros: 100_000, month_micros: 100_000, cap_micros: 15_000_000, cap_used_pct: 0.7 }),
];
const SCRIPTS: ScriptSummary[] = [
  { id: 20, project_id: 1, title: "The Keeper's Log", chunks: 3, chars: 3000, model_id: null },
  { id: 10, project_id: 2, title: "011", chunks: 8, chars: 15000, model_id: null },
  { id: 11, project_id: 2, title: "012", chunks: 8, chars: 15000, model_id: null },
];

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
});

const render = async (selected: number | null) => {
  await act(async () => {
    root.render(<CostsPage projects={PROJECTS} scripts={SCRIPTS} selected={selected} />);
  });
};

describe("the costs page", () => {
  it("lists the episodes of the project in hand, even when it arrives late", async () => {
    // The shell's selection is not known on the first render.
    await render(null);
    await render(2);

    expect(host.querySelector(".cost-episodes")?.textContent).toContain("011");
    expect(host.querySelector(".cost-episodes")?.textContent).not.toContain("The Keeper's Log");
    expect(host.textContent).toContain("012 has a script but nothing generated yet.");
  });

  it("shows each project's month against its cap", async () => {
    await render(2);
    const row = [...host.querySelectorAll("tbody tr")].find((r) => r.textContent?.includes("HINDSIGHT"));
    expect(row?.textContent).toContain("$0.10");
    expect(row?.textContent).toContain("0.7% of $15");
  });

  it("works out spend per finished minute across the account", async () => {
    await render(2);
    // $0.40 over 8 minutes: each episode's spend at its own $0.05 a minute.
    expect(host.textContent).toContain("$0.0500");
    expect(host.textContent).toContain("across 8 minutes of finished audio");
  });
});
