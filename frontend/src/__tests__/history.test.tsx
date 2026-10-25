// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { History } from "../components/History";
import type { ActivityEvent } from "../types";

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

const events: ActivityEvent[] = Array.from({ length: 8 }, (_, i) => ({
  at: new Date(2026, 9, 3 + i, 18, 25).toISOString(),
  kind: "generated",
  icon: "🎙️",
  text: `Event ${i + 1}`,
}));

describe("an episode's history", () => {
  it("shows the latest few, and all of it on request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(events), { status: 200 })));
    await act(async () => root.render(<History scriptId={17} refreshKey={0} />));

    expect(host.querySelectorAll(".history li")).toHaveLength(6);
    expect(host.textContent).toContain("Sat 3 Oct 18:25");

    const more = [...host.querySelectorAll("button")].find((b) => b.textContent === "Show all 8")!;
    await act(async () => more.click());
    expect(host.querySelectorAll(".history li")).toHaveLength(8);
  });
});
