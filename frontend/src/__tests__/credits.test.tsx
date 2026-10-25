// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChunkList } from "../components/ChunkList";
import { CreditsMeter } from "../components/Rail";
import type { ChunkRow, Take } from "../types";

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

const render = async (node: React.ReactNode) => {
  await act(async () => root.render(node));
};

describe("credits left", () => {
  it("shows what is left, of what, and when it resets", async () => {
    await render(
      <CreditsMeter
        credits={{
          available: true,
          used: 58_780,
          limit: 100_000,
          remaining: 41_220,
          resets_at: "2026-11-01T00:00:00+00:00",
          tier: "creator",
        }}
      />,
    );
    expect(host.textContent).toContain("41,220");
    expect(host.textContent).toContain("of 100,000 · resets 1 Nov");
    expect(host.querySelector(".meter")?.classList.contains("warn")).toBe(false);
  });

  it("turns to a warning with less than a tenth left", async () => {
    await render(
      <CreditsMeter
        credits={{ available: true, used: 95_000, limit: 100_000, remaining: 5_000, resets_at: null, tier: null }}
      />,
    );
    expect(host.querySelector(".meter")?.classList.contains("warn")).toBe(true);
  });
});

describe("a take that failed", () => {
  function failed(over: Partial<Take> = {}): Take {
    return {
      id: 9,
      ordinal: 1,
      status: "failed",
      billed_chars: 0,
      cost_micros: 0,
      duration_s: null,
      model_id: "eleven_v3",
      in_cut: false,
      verify_status: "unverified",
      verify_accepted: false,
      findings: [],
      error: {
        headline: "ElevenLabs ran out of credits",
        detail: "45 credits were left, and it needed 312. Nothing was charged for it.",
        fix: "Top up on ElevenLabs or wait for the monthly reset, then generate it again.",
      },
      ...over,
    };
  }
  const chunk = (takes: Take[]): ChunkRow => ({
    ordinal: 8,
    text: "The last words.",
    chars: 15,
    source: "paragraph",
    target_start_s: null,
    takes,
  });

  it("says why it has no audio, and what to do", async () => {
    await render(
      <ChunkList chunks={[chunk([failed()])]} scriptId={17} onChange={vi.fn()} onRegenerate={vi.fn()} busy={false} />,
    );
    expect(host.textContent).toContain("ElevenLabs ran out of credits");
    expect(host.textContent).toContain("45 credits were left, and it needed 312");
    expect(host.textContent).toContain("wait for the monthly reset");
  });
});
