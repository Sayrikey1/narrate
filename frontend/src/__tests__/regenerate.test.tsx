// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChunkList } from "../components/ChunkList";
import { RegeneratePanel } from "../components/RegeneratePanel";
import type { ChunkRow, Finding, RegenerateQuote, Take } from "../types";

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const render = async (node: React.ReactNode) => {
  await act(async () => {
    root.render(node);
  });
};

const click = async (element: Element | null | undefined) => {
  await act(async () => {
    (element as HTMLElement).click();
  });
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

function quote(over: Partial<RegenerateQuote> = {}): RegenerateQuote {
  return {
    dry_run: true,
    plans: [
      {
        ordinal: 5,
        chunk_id: 5,
        chars: 2200,
        price_micros: 110_000,
        model_id: "eleven_v3",
        cut_take: 1,
        cut_status: "suspect",
        blocker: null,
      },
    ],
    attempts: 1,
    worst_micros: 110_000,
    worst_usd: "$0.1100",
    checked_with: "speech-to-text",
    mock: false,
    ...over,
  };
}

function finding(over: Partial<Finding> = {}): Finding {
  return {
    severity: "fail",
    kind: "missing",
    summary: '"them" not spoken (0.94s gap)',
    expected: "them",
    start_s: 82.9,
    ...over,
  };
}

function take(over: Partial<Take> = {}): Take {
  return {
    id: 16,
    ordinal: 1,
    status: "succeeded",
    billed_chars: 302,
    cost_micros: 15_100,
    duration_s: 134.24,
    model_id: "eleven_v3",
    in_cut: true,
    verify_status: "suspect",
    findings: [finding()],
    ...over,
  };
}

function chunk(takes: Take[]): ChunkRow {
  return {
    ordinal: 5,
    text: "So the smart ones figure this out.",
    chars: 2200,
    source: "paragraph",
    target_start_s: null,
    takes,
  };
}

function stubQuote(body: RegenerateQuote) {
  const fetch = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify(body), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("regenerating is priced before it is paid for", () => {
  it("asks for a price without confirming, then names the amount on the button", async () => {
    const fetch = stubQuote(quote());
    const onRun = vi.fn(async () => undefined);
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={onRun} onClose={() => {}} />,
    );

    await click(button("Price it"));

    const sent = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(sent.confirm).toBe(false);
    expect(onRun).not.toHaveBeenCalled();
    expect(button("Regenerate for up to $0.1100")).toBeTruthy();

    await click(button("Regenerate for up to $0.1100"));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ chunks: [5], confirm: true }));
  });

  it("throws the price away when an option changes, so the button is never stale", async () => {
    stubQuote(quote());
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    await click(button("Price it"));
    expect(button("Regenerate for up to")).toBeTruthy();

    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(button("Regenerate for up to")).toBeFalsy();
    expect(button("Price it")).toBeTruthy();
  });

  it("will not spend where a request's outcome is unknown", async () => {
    const blocked = quote();
    blocked.plans[0].blocker = "It may already have been billed.";
    stubQuote(blocked);
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    await click(button("Price it"));

    expect(host.textContent).toContain("may already have been billed");
    expect((button("Regenerate for up to") as HTMLButtonElement).disabled).toBe(true);
  });

  it("says plainly when nothing will be billed", async () => {
    stubQuote(quote({ mock: true }));
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    await click(button("Price it"));
    expect(host.textContent).toContain("nothing is billed");
  });
});

describe("found in review", () => {
  it("drops a price that arrives after the options changed", async () => {
    // The price request is held open, the options change, then it answers.
    let answer: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (answer = resolve))),
    );
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    await click(button("Price it"));

    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      answer(new Response(JSON.stringify(quote()), { status: 200 }));
    });

    // The late answer was for three tries, not one: it must not reach the button.
    expect(button("Regenerate for up to")).toBeFalsy();
  });

  it("offers an explicit override where an outcome is unknown", async () => {
    const blocked = quote();
    blocked.plans[0].blocker = "It may already have been billed.";
    const fetch = stubQuote(blocked);
    const onRun = vi.fn(async () => undefined);
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={onRun} onClose={() => {}} />,
    );
    await click(button("Price it"));
    expect((button("Regenerate for up to") as HTMLButtonElement).disabled).toBe(true);

    const override = [...host.querySelectorAll("label")].find((l) =>
      l.textContent?.includes("regenerate anyway"),
    );
    await click(override?.querySelector("input"));
    // Changing the answer discards the old price; ask again, then it may run.
    await click(button("Price it"));
    const sent = JSON.parse(String(fetch.mock.calls[1][1]?.body));
    expect(sent.accept_unknown).toBe(true);
    await click(button("Regenerate for up to"));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ accept_unknown: true }));
  });

  it("moves focus into the panel when it opens", async () => {
    await render(
      <RegeneratePanel scriptId={5} chunks={[chunk([take()])]} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    expect(host.contains(document.activeElement)).toBe(true);
  });
});

describe("a flagged take says what and where", () => {
  it("shows the verdict and each problem with a time to play from", async () => {
    await render(
      <ChunkList chunks={[chunk([take()])]} scriptId={5} busy={false} onChange={() => {}} onRegenerate={vi.fn()} />,
    );
    expect(host.textContent).toContain("flagged · 1");
    expect(host.textContent).toContain('"them" not spoken');
    expect(button("1:22")).toBeTruthy();
  });

  it("never draws a tick for a clean check", async () => {
    await render(
      <ChunkList
        chunks={[chunk([take({ verify_status: "clear", findings: [] })])]}
        scriptId={5}
        busy={false}
        onChange={() => {}}
        onRegenerate={vi.fn()}
      />,
    );
    expect(host.textContent).toContain("no issues found");
  });

  it("keeps a finding at the very start of a take playable", async () => {
    await render(
      <ChunkList
        chunks={[chunk([take({ findings: [finding({ start_s: 0 })] })])]}
        scriptId={5}
        busy={false}
        onChange={() => {}}
        onRegenerate={vi.fn()}
      />,
    );
    expect(host.textContent).not.toContain("NaN");
    expect(button("0:00")).toBeTruthy();
  });

  it("lists a few problems and counts the rest, rather than reciting them all", async () => {
    const many = Array.from({ length: 7 }, (_, i) => finding({ start_s: i * 10 }));
    await render(
      <ChunkList
        chunks={[chunk([take({ findings: many })])]}
        scriptId={5}
        busy={false}
        onChange={() => {}}
        onRegenerate={vi.fn()}
      />,
    );
    expect(host.querySelectorAll(".findings button").length).toBe(4);
    expect(host.textContent).toContain("and 3 more on this take");
  });

  it("offers to regenerate a chunk that has audio", async () => {
    await render(
      <ChunkList chunks={[chunk([take()])]} scriptId={5} busy={false} onChange={() => {}} onRegenerate={vi.fn()} />,
    );
    await click(button("Regenerate…"));
    expect(button("Price it")).toBeTruthy();
  });
});

describe("found checking the review fixes", () => {
  it("keeps the Regenerate… button focusable while a run is going", async () => {
    // Focus returns to it when the panel closes — which happens right after a
    // regeneration starts, when the page is busy. A disabled button cannot
    // take focus, so it is marked aria-disabled instead, and ignores clicks.
    await render(
      <ChunkList chunks={[chunk([take()])]} scriptId={5} busy={true} onChange={() => {}} onRegenerate={vi.fn()} />,
    );
    const toggle = button("Regenerate…") as HTMLButtonElement;
    expect(toggle.disabled).toBe(false);
    expect(toggle.getAttribute("aria-disabled")).toBe("true");

    toggle.focus();
    expect(document.activeElement).toBe(toggle);

    await click(toggle);
    expect(button("Price it")).toBeFalsy();
  });
});

describe("fixing everything flagged at once", () => {
  it("prices every flagged chunk, keeps trying, and rebuilds the episode", async () => {
    const fetch = stubQuote(quote());
    const three = { ...chunk([take()]), ordinal: 3 };
    await render(
      <RegeneratePanel
        scriptId={5}
        chunks={[three, chunk([take()])]}
        busy={false}
        onRun={vi.fn()}
        onClose={() => {}}
      />,
    );

    // Rewording is one chunk's words, so it is not offered for several.
    expect(host.textContent).not.toContain("Change the words first");

    await click(button("Price it"));
    const sent = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(sent).toMatchObject({ chunks: [3, 5], attempts: 3, export: true, confirm: false });
  });
});

describe("the fix-all panel never spends past its button", () => {
  it("keeps to the chunks it was opened for, and sends the quote as the limit", async () => {
    stubQuote(quote({ worst_micros: 330_000, worst_usd: "$0.3300" }));
    const onRun = vi.fn(async () => undefined);
    const five = chunk([take()]);
    const panel = (rows: ChunkRow[]) => (
      <RegeneratePanel scriptId={5} chunks={rows} busy={false} onRun={onRun} onClose={() => {}} />
    );
    await render(panel([five]));
    await click(button("Price it"));

    // A refresh flags chunk 9 while the panel is open.
    await render(panel([five, { ...five, ordinal: 9 }]));
    await click(button("Regenerate for up to $0.3300"));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ chunks: [5], max_spend_usd: 0.33, confirm: true }),
    );
  });

  it("skips a blocked chunk and still fixes the others", async () => {
    const mixed = quote();
    mixed.plans = [
      { ...mixed.plans[0], ordinal: 5, blocker: "It may already have been billed." },
      { ...mixed.plans[0], ordinal: 8, blocker: "Take 3 of chunk 8 got no answer." },
      { ...mixed.plans[0], ordinal: 9, blocker: null },
    ];
    stubQuote(mixed);
    const rows = [5, 8, 9].map((ordinal) => ({ ...chunk([take()]), ordinal }));
    await render(
      <RegeneratePanel scriptId={5} chunks={rows} busy={false} onRun={vi.fn()} onClose={() => {}} />,
    );
    await click(button("Price it"));

    expect(host.textContent).toContain("Chunk 5 will be skipped");
    expect(host.textContent).toContain("Chunk 8 will be skipped");
    expect((button("Regenerate for up to") as HTMLButtonElement).disabled).toBe(false);
  });
});
