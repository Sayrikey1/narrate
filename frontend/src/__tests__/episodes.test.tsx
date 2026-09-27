// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeletePanel } from "../components/DeletePanel";
import { ReplacePanel } from "../components/ReplacePanel";
import type { DeleteBody, DeletePlan, ReplacePlan } from "../types";

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

function plan(over: Partial<DeletePlan> = {}): DeletePlan {
  return {
    kind: "episode",
    id: 5,
    name: "Episode 1",
    episodes: [5],
    takes: 12,
    spend_micros: 800_200,
    spend_usd: "$0.8002",
    files: 30,
    file_bytes: 250_000_000,
    running: [],
    done: false,
    files_deleted: 0,
    ...over,
  };
}

describe("deleting", () => {
  it("shows what stays before anything is deleted, then deletes on confirm", async () => {
    const run = vi.fn(async (body: DeleteBody) => plan({ done: Boolean(body.confirm) }));
    const onDone = vi.fn();
    await render(<DeletePanel what="episode" run={run} onDone={onDone} onClose={() => {}} />);

    expect(run).toHaveBeenCalledWith({});
    expect(host.textContent).toContain("$0.8002");
    expect(host.textContent).toContain("stays on the record");
    expect(onDone).not.toHaveBeenCalled();

    await click(button("Delete episode"));
    expect(run).toHaveBeenLastCalledWith({ confirm: true, delete_files: false });
    expect(onDone).toHaveBeenCalled();
  });

  it("deletes files only when asked", async () => {
    const run = vi.fn(async (_body: DeleteBody) => plan());
    await render(<DeletePanel what="episode" run={run} onDone={() => {}} onClose={() => {}} />);

    await click(host.querySelector("input[type=checkbox]"));
    await click(button("Delete episode"));

    expect(run).toHaveBeenLastCalledWith({ confirm: true, delete_files: true });
  });

  it("will not delete while a generation is running", async () => {
    await render(
      <DeletePanel
        what="episode"
        run={async () => plan({ running: [5] })}
        onDone={() => {}}
        onClose={() => {}}
      />,
    );
    expect((button("Delete episode") as HTMLButtonElement).disabled).toBe(true);
  });
});

function quote(over: Partial<ReplacePlan> = {}): ReplacePlan {
  return {
    script_id: 5,
    title: "Episode 1",
    unchanged: false,
    kept: 9,
    kept_takes: 11,
    kept_regenerated: 0,
    new: 1,
    retired: 1,
    retired_takes: 1,
    retired_to: 12,
    beats_detached: 0,
    slots_dropped: 0,
    chunks_to_generate: 1,
    chars: 1603,
    effects_to_generate: 0,
    quote_micros: 80_150,
    quote_usd: "$0.0802",
    warnings: [],
    done: false,
    ...over,
  };
}

describe("replacing a script", () => {
  it("prices the change without applying it, then replaces on confirm", async () => {
    const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify(quote({ done: body.confirm })), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    const onDone = vi.fn();
    await render(<ReplacePanel scriptId={5} busy={false} onDone={onDone} onClose={() => {}} />);

    const area = host.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(area, "The new script.");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(button("Price it"));

    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body)).confirm).toBe(false);
    expect(host.textContent).toContain("Keeps 9");
    expect(host.textContent).toContain("$0.0802");
    expect(onDone).not.toHaveBeenCalled();

    await click(button("Replace the script"));
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body)).confirm).toBe(true);
    expect(onDone).toHaveBeenCalled();
  });

  const type = async (text: string) => {
    const area = host.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(area, text);
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };

  it("says when effect slots go with a dropped passage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(quote({ slots_dropped: 2 })), { status: 200 })),
    );
    await render(<ReplacePanel scriptId={5} busy={false} onDone={() => {}} onClose={() => {}} />);
    await type("The new script.");
    await click(button("Price it"));

    expect(host.textContent).toContain("Removes 2 effect slot(s)");
  });

  it("keeps the force box on screen once it is ticked", async () => {
    // Ticking it clears the refusal it answers; the box must not go with it.
    const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return body.force
        ? new Response(JSON.stringify(quote()), { status: 200 })
        : new Response(
            JSON.stringify({ detail: "A generation is still running for this episode." }),
            { status: 409 },
          );
    });
    vi.stubGlobal("fetch", fetch);
    await render(<ReplacePanel scriptId={5} busy={false} onDone={() => {}} onClose={() => {}} />);
    await type("The new script.");
    await click(button("Price it"));
    await click(host.querySelector("input[type=checkbox]"));

    const box = host.querySelector("input[type=checkbox]") as HTMLInputElement | null;
    expect(box?.checked).toBe(true);
    await click(button("Price it"));
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body)).force).toBe(true);
    expect(host.textContent).toContain("Keeps 9");
  });
});
