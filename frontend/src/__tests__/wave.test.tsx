// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePeaks, WaveBars } from "../components/Wave";

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

describe("a waveform", () => {
  it("draws a bar a peak, the played part lit, and silence as a stub", async () => {
    await act(async () => root.render(<WaveBars peaks={[1, 0, 0.5, 0.25]} played={0.5} />));
    const bars = [...host.querySelectorAll<HTMLElement>(".wave-bars > span")];

    expect(bars.map((b) => b.classList.contains("on"))).toEqual([true, true, false, false]);
    expect(bars.map((b) => b.style.height)).toEqual(["100%", "16%", "58%", "37%"]);
  });

  it("is fetched once however often it is drawn", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ peaks: [0.5, 1] }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    function Drawn() {
      const peaks = usePeaks("/api/peaks/take/1?n=2");
      return peaks ? <WaveBars peaks={peaks} /> : null;
    }

    await act(async () => root.render(<><Drawn /><Drawn /></>));
    await act(async () => root.render(<Drawn />));
    expect(host.querySelectorAll(".wave-bars > span")).toHaveLength(2);
    expect(fetch.mock.calls.length).toBeLessThanOrEqual(2);

    await act(async () => root.render(<><Drawn /><Drawn /><Drawn /></>));
    expect(fetch.mock.calls.length).toBeLessThanOrEqual(2);
  });
});
