// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PublishPage } from "../pages/Publish";
import { parseStamp } from "../time";
import type { Publish } from "../types";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  URL.createObjectURL = vi.fn(() => "blob:stub");
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

const render = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(node);
  });
};

function pack(over: Partial<Publish["data"]> = {}): Publish {
  return {
    markdown: "# An Episode — publish pack\n\n## Chapters\n",
    data: {
      script_id: 1,
      script_title: "An Episode",
      project: "A Channel",
      runtime_s: 1246,
      complete: true,
      title: {
        chosen: "The Chosen Title",
        over_length: false,
        candidates: [
          {
            id: 1,
            text: "The Chosen Title",
            formula: "Concise",
            rationale: "plain",
            accepted: true,
            proposed: false,
            over_length: false,
          },
          {
            id: 2,
            text: "A Rejected Title",
            formula: "Superlative",
            rationale: "loud",
            accepted: false,
            proposed: true,
            over_length: false,
          },
        ],
      },
      description: "Some words.",
      boilerplate: "",
      tags: ["wealth", "money"],
      chapters: {
        usable: true,
        problems: [],
        text: "0:00 The Hook\n1:46 The Middle\n3:26 The End",
        entries: [
          { stamp: "0:00", start_s: 0, title: "The Hook", chunk_ordinal: 1 },
          { stamp: "1:46", start_s: 106, title: "The Middle", chunk_ordinal: 2 },
          { stamp: "3:26", start_s: 206, title: "The End", chunk_ordinal: 3 },
        ],
      },
      thumbnail: null,
      briefs: [],
      retention: {
        good_pct: 38,
        great_pct: 43,
        good_hold_s: 473,
        great_hold_s: 535,
        extrapolated: false,
      },
      ...over,
    },
  };
}

function stubFetch(body: Publish): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

describe("a chapter stamp is clickable", () => {
  /** The plan writes `HH:MM:SS`; a chapter stamp writes `M:SS`, because YouTube
   *  rejects `00:04:31` where it accepts `4:31`. One parser has to read both, or
   *  clicking a chapter row silently does nothing. */
  it("reads both shapes of timecode", () => {
    expect(parseStamp("0:00")).toBe(0);
    expect(parseStamp("4:31")).toBe(271);
    expect(parseStamp("1:02:03")).toBe(3723);
    expect(parseStamp("00:01:45.720")).toBeCloseTo(105.72);
  });

  it("refuses anything that is not one", () => {
    expect(parseStamp("The Hook")).toBeNull();
    expect(parseStamp("")).toBeNull();
    expect(parseStamp("12")).toBeNull();
  });
});

describe("the publish pack page", () => {
  it("shows the chapters with their measured times", async () => {
    stubFetch(pack());
    await render(<PublishPage scriptId={1} script={null} />);

    const tabs = [...host.querySelectorAll("[role=tab]")].map((t) => t.textContent);
    expect(tabs).toContain("Packaging");

    const packaging = [...host.querySelectorAll("[role=tab]")].find(
      (t) => t.textContent === "Packaging",
    ) as HTMLElement;
    await act(async () => packaging.click());

    expect(host.textContent).toContain("The Hook");
    expect(host.textContent).toContain("3:26");
  });

  it("reports every broken chapter rule rather than hiding the list", async () => {
    stubFetch(
      pack({
        chapters: {
          usable: false,
          problems: ["The first chapter is at 1:46, but YouTube requires one at 0:00"],
          text: "",
          entries: [{ stamp: "1:46", start_s: 106, title: "Late", chunk_ordinal: 2 }],
        },
      }),
    );
    await render(<PublishPage scriptId={1} script={null} />);
    const packaging = [...host.querySelectorAll("[role=tab]")].find(
      (t) => t.textContent === "Packaging",
    ) as HTMLElement;
    await act(async () => packaging.click());

    expect(host.textContent).toContain("YouTube would not accept this");
    expect(host.textContent).toContain("requires one at 0:00");
  });

  it("offers a choice only on titles that are not already chosen", async () => {
    stubFetch(pack());
    await render(<PublishPage scriptId={1} script={null} />);
    const packaging = [...host.querySelectorAll("[role=tab]")].find(
      (t) => t.textContent === "Packaging",
    ) as HTMLElement;
    await act(async () => packaging.click());

    const buttons = [...host.querySelectorAll("button")].filter(
      (b) => b.textContent === "choose",
    );
    // Two candidates, one already chosen.
    expect(buttons).toHaveLength(1);
    expect(host.textContent).toContain("chosen");
  });

  it("says what to do when no thumbnail brief exists", async () => {
    stubFetch(pack());
    await render(<PublishPage scriptId={1} script={null} />);
    const packaging = [...host.querySelectorAll("[role=tab]")].find(
      (t) => t.textContent === "Packaging",
    ) as HTMLElement;
    await act(async () => packaging.click());

    expect(host.textContent).toContain("No brief yet");
    expect(host.textContent).toContain("No image is generated");
  });

  it("marks an extrapolated retention target rather than presenting it as published", async () => {
    stubFetch(
      pack({
        retention: {
          good_pct: 55,
          great_pct: 60,
          good_hold_s: 64,
          great_hold_s: 70,
          extrapolated: true,
        },
      }),
    );
    await render(<PublishPage scriptId={1} script={null} />);
    const packaging = [...host.querySelectorAll("[role=tab]")].find(
      (t) => t.textContent === "Packaging",
    ) as HTMLElement;
    await act(async () => packaging.click());

    expect(host.textContent).toContain("Extrapolated");
  });

  it("surfaces a failed load instead of an empty page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 500 })),
    );
    await render(<PublishPage scriptId={1} script={null} />);
    expect(host.textContent).toContain("Could not load the publish pack");
  });
});
