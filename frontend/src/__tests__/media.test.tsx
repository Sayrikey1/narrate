// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { entriesOf } from "../mediaFiles";
import { MediaPage } from "../pages/Media";
import type { MediaFile, Project, ProjectMedia, SavedArchive, ScriptSummary, Slot, Timeline } from "../types";

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

function file(name: string, over: Partial<MediaFile> = {}): MediaFile {
  return {
    name,
    path: `@export/10/${name}`,
    kind: "export",
    bytes: 1000,
    label: "",
    duration_s: null,
    playable: /\.(mp3|wav)$/.test(name),
    take_id: null,
    effect_id: null,
    script_id: 10,
    modified: 1_761_000_000,
    ...over,
  };
}

/** Episode 004 exported, a take of 005, and two library effects. */
function media(over: Partial<ProjectMedia> = {}): ProjectMedia {
  return {
    project_id: 1,
    project: "HINDSIGHT",
    files: 7,
    bytes: 7000,
    export_folder: "/Users/me/projects/hindsight",
    groups: [
      {
        title: "004 — export",
        kind: "export",
        bytes: 4000,
        files: [
          file("004.wav"),
          file("004_fx.mp3"),
          file("HINDSIGHT_00-00-00-000_00-01-37-920.mp3"),
          file("plan.md"),
        ],
      },
      {
        title: "Effects",
        kind: "effect",
        bytes: 2000,
        files: [
          file("camera-flash.mp3", { kind: "effect", path: "fx/a.mp3", script_id: null, effect_id: 3, label: "camera-flash · 1 placement" }),
          file("rain.mp3", { kind: "effect", path: "fx/b.mp3", script_id: null, effect_id: 4, label: "rain · 1 placement" }),
        ],
      },
      {
        title: "Takes",
        kind: "take",
        bytes: 1000,
        files: [
          file("chunk-001-take-02.mp3", {
            kind: "take",
            path: "HINDSIGHT/script-11/chunk-001-take-02.mp3",
            script_id: 11,
            take_id: 7,
            duration_s: 97,
            label: "chunk 1 · take 2 · in the cut",
          }),
        ],
      },
    ],
    ...over,
  };
}

const project: Project = {
  id: 1,
  name: "HINDSIGHT",
  voice_id: "v1",
  model_id: "eleven_v3",
  spend_micros: 0,
  waste_pct: 0,
  month_micros: 0,
  cap_micros: null,
  cap_used_pct: 0,
};
const scripts: ScriptSummary[] = [
  { id: 10, project_id: 1, title: "004", chunks: 1, chars: 1000, model_id: null },
  { id: 11, project_id: 1, title: "005", chunks: 1, chars: 1000, model_id: null },
];
const slot = (effect_id: number): Slot => ({
  id: effect_id,
  at_chunk_ordinal: 1,
  description: "camera flashes",
  duration_s: 2,
  loop: false,
  source: "marker",
  accepted: true,
  note: "",
  effect_id,
  slug: null,
  cost_micros: 0,
});

/** The listing for the media call, `saved` for a POST, nothing for the rest. */
function stub(listing: ProjectMedia, saved?: SavedArchive) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.method === "POST" ? saved : url.includes("/media") ? listing : [];
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const render = async (scriptId = 10, slots: Slot[] = [slot(3)]) => {
  await act(async () => {
    root.render(
      <MediaPage project={project} scriptId={scriptId} scripts={scripts} timeline={null} slots={slots} />,
    );
  });
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

const names = () => [...host.querySelectorAll(".media-row-name")].map((n) => n.textContent);

describe("an episode's zip goes where the exports go", () => {
  it("is saved beside the episode folders, not downloaded, when exports have a folder", async () => {
    const fetch = stub(media(), {
      name: "004.zip",
      folder: "/Users/me/projects/hindsight",
      unzips_to: "004",
      replaced: true,
      bytes: 1000,
    });
    await render();
    expect(host.querySelector('a[href="/api/exports/10/archive"]')).toBeNull();

    await act(async () => {
      button("Save zip")?.click();
    });

    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/exports/10/archive");
    expect(host.textContent).toContain("Replaced 004.zip in hindsight");
  });

  it("is a download when exports stay in narrate's own folder", async () => {
    stub(media({ export_folder: null }));
    await render();
    expect(host.querySelector('a[href="/api/exports/10/archive"]')).not.toBeNull();
    expect(button("Save zip")).toBeUndefined();
  });
});

describe("what each file is", () => {
  it("tells masters, pieces, documents, takes and effects apart", () => {
    const timeline = {
      script_id: 10,
      title: "004",
      project: "HINDSIGHT",
      runtime_s: 97.92,
      complete: true,
      entries: [
        {
          index: 1,
          kind: "narration" as const,
          start: "",
          end: "",
          start_s: 0,
          end_s: 97.92,
          duration_s: 97.92,
          label: "The keeper wrote…",
          chunk_ordinal: 1,
          take_id: 7,
          slot_id: null,
          effect_id: null,
          generated: true,
          target_s: null,
          drift_s: null,
        },
      ],
    } satisfies Timeline;
    const byName = Object.fromEntries(entriesOf(media(), timeline).map((e) => [e.file.name, e]));

    expect(byName["004.wav"]?.kind).toBe("master");
    expect(byName["004_fx.mp3"]?.meta).toContain("master with effects mixed in");
    expect(byName["HINDSIGHT_00-00-00-000_00-01-37-920.mp3"]).toMatchObject({
      kind: "piece",
      meta: "chunk 1 · 0:00 – 1:37",
    });
    expect(byName["plan.md"]).toMatchObject({ kind: "document", meta: "editing plan — what plays when" });
    expect(byName["chunk-001-take-02.mp3"]).toMatchObject({ kind: "take", chunk: 1, take: 2, inCut: true });
    expect(byName["rain.mp3"]?.kind).toBe("effect");
  });
});

describe("finding a file", () => {
  it("shows this episode's files, and the effects it places, until asked for the project", async () => {
    stub(media());
    await render();

    expect(names()).toContain("004.wav");
    expect(names()).toContain("camera-flash.mp3");
    // Another episode's take, and an effect this one never places.
    expect(names()).not.toContain("Chunk 1 · take 2");
    expect(names()).not.toContain("rain.mp3");

    await act(async () => button("Show the whole project")?.click());
    expect(host.textContent).toContain("Takes for 005");
    expect(host.textContent).toContain("Effect library");
  });

  it("counts each kind, and filters to one", async () => {
    stub(media());
    await render();

    expect(button("🎧 Masters")?.textContent).toContain("2");
    await act(async () => button("📄 Documents")?.click());
    expect(names()).toEqual(["plan.md"]);
  });

  it("searches names and descriptions", async () => {
    stub(media());
    await render();
    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(search, "effects mixed");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(names()).toEqual(["004_fx.mp3"]);
  });
});
