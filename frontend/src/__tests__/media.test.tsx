// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MediaLibrary } from "../components/MediaLibrary";
import type { ProjectMedia, SavedArchive } from "../types";

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

function media(over: Partial<ProjectMedia> = {}): ProjectMedia {
  return {
    project_id: 1,
    project: "HINDSIGHT",
    files: 1,
    bytes: 1000,
    export_folder: "/Users/me/projects/hindsight",
    groups: [
      {
        title: "004 — export",
        kind: "export",
        bytes: 1000,
        files: [
          {
            name: "004.mp3",
            path: "@export/10/004.mp3",
            kind: "export",
            bytes: 1000,
            label: "",
            duration_s: null,
            playable: false,
            take_id: null,
            effect_id: null,
            script_id: 10,
          },
        ],
      },
    ],
    ...over,
  };
}

function stub(listing: ProjectMedia, saved?: SavedArchive) {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = init?.method === "POST" ? saved : listing;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const render = async () => {
  await act(async () => {
    root.render(<MediaLibrary projectId={1} />);
  });
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

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

    const [url, init] = fetch.mock.calls[1];
    expect([url, init?.method]).toEqual(["/api/exports/10/archive", "POST"]);
    expect(host.textContent).toContain("Replaced 004.zip in hindsight");
  });

  it("is a download when exports stay in narrate's own folder", async () => {
    stub(media({ export_folder: null }));
    await render();
    expect(host.querySelector('a[href="/api/exports/10/archive"]')).not.toBeNull();
    expect(button("Save zip")).toBeUndefined();
  });
});
