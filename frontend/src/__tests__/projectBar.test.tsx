// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectBar } from "../components/ProjectBar";
import type { Model, Project } from "../types";

const updateProject = vi.fn(async () => ({ rejected_settings: [] }));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: {
    voices: () => Promise.resolve([]),
    voicePreview: (id: string) => `/api/voices/${id}/preview`,
    updateProject: (...args: unknown[]) => updateProject(...(args as [])),
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

function project(id: number, name: string, model_id: string, voice_id: string): Project {
  return { id, name, model_id, voice_id, spend_micros: 0, waste_pct: 0, cap_micros: null, cap_used_pct: 0 };
}

const MODELS = [model("eleven_multilingual_v2", { long_form: "best" }), model("eleven_v3", { default: true })];
const PROJECTS = [
  project(1, "Old", "eleven_multilingual_v2", "voice-1"),
  project(2, "New", "eleven_v3", "voice-2"),
];

const bar = (selected: number) => (
  <ProjectBar projects={PROJECTS} models={MODELS} selected={selected} onSelect={() => {}} onChanged={() => {}} />
);

describe("the project profile form", () => {
  it("edits the project now selected, not the one it was opened on", async () => {
    await act(async () => root.render(bar(1)));
    const open = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Voice & delivery"));
    await act(async () => (open as HTMLButtonElement).click());

    // Another project is selected while the form is open.
    await act(async () => root.render(bar(2)));
    const form = host.querySelector("form") as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(updateProject).toHaveBeenCalledWith(
      2,
      expect.objectContaining({ model_id: "eleven_v3", voice_id: "voice-2" }),
    );
  });
});
