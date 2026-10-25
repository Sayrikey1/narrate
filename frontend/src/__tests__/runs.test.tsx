// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Topbar } from "../components/Topbar";
import { useActiveRuns } from "../hooks";
import type { ActiveRun } from "../types";

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
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const run = (over: Partial<ActiveRun> = {}): ActiveRun => ({
  script_id: 18,
  title: "012",
  phase: "narration",
  done: 5,
  total: 8,
  failed: 0,
  spent_micros: 840_000,
  ...over,
});

describe("the run pill", () => {
  it("says what is generating and how far it has got, on every page", async () => {
    await act(async () =>
      root.render(
        <Topbar pattern="/costs" project={null} episodes={[]} episode={null} runs={[run()]} onSearch={vi.fn()} />,
      ),
    );
    const pill = host.querySelector("a.run-pill");
    expect(pill?.textContent).toContain("Generating 012");
    expect(pill?.textContent).toContain("5/8 · $0.84");
    expect(pill?.getAttribute("href")).toBe("/script/18");
  });

  it("says a check is a check", async () => {
    await act(async () =>
      root.render(
        <Topbar
          pattern="/"
          project={null}
          episodes={[]}
          episode={null}
          runs={[run({ phase: "checking", done: null, total: null })]}
          onSearch={vi.fn()}
        />,
      ),
    );
    expect(host.querySelector("a.run-pill")?.textContent).toContain("Checking 012");
  });
});

describe("watching the runs", () => {
  it("fetches the figures again when a run finishes", async () => {
    vi.useFakeTimers();
    const replies = [[run()], []];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(replies.shift() ?? []), { status: 200 })),
    );
    const onFinished = vi.fn();
    function Watcher() {
      const runs = useActiveRuns(onFinished);
      return <span>{runs.length}</span>;
    }

    await act(async () => root.render(<Watcher />));
    expect(host.textContent).toBe("1");
    expect(onFinished).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(host.textContent).toBe("0");
    expect(onFinished).toHaveBeenCalledTimes(1);
  });
});
