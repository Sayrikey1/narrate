// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuickSwitcher } from "../components/QuickSwitcher";
import type { Project, ScriptSummary } from "../types";

const projects: Project[] = [
  { id: 1, name: "HINDSIGHT", voice_id: "v", model_id: "eleven_v3", spend_micros: 0, waste_pct: 0, month_micros: 0, default_tags: [], description_boilerplate: "", cap_micros: null, cap_used_pct: 0 },
  { id: 2, name: "Wealth Quadrants", voice_id: "v", model_id: "eleven_v3", spend_micros: 0, waste_pct: 0, month_micros: 0, default_tags: [], description_boilerplate: "", cap_micros: null, cap_used_pct: 0 },
];
const scripts: ScriptSummary[] = [
  { id: 16, project_id: 1, title: "010", chunks: 7, chars: 14000, model_id: null },
  { id: 17, project_id: 1, title: "011", chunks: 8, chars: 15000, model_id: null },
];

let host: HTMLDivElement;
let root: Root;
const onProject = vi.fn();

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button">before</button>
      <QuickSwitcher
        open={open}
        onOpenChange={setOpen}
        projects={projects}
        scripts={scripts}
        episode={scripts[1]!}
        onProject={onProject}
      />
    </>
  );
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  history.replaceState(null, "", "/");
  onProject.mockClear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const press = async (key: string, target: EventTarget = window, extra: KeyboardEventInit = {}) => {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...extra }));
  });
};

const type = async (text: string) => {
  const input = document.activeElement as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

describe("the quick switcher", () => {
  it("opens on ⌘K, finds an episode by name, and goes there on Enter", async () => {
    await act(async () => root.render(<Harness />));
    expect(host.querySelector('[role="dialog"]')).toBeNull();

    await press("k", window, { metaKey: true });
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.activeElement?.getAttribute("role")).toBe("combobox");

    await type("010");
    const options = [...host.querySelectorAll('[role="option"]')].map((o) => o.textContent);
    expect(options[0]).toContain("010");

    await press("Enter", document.activeElement!);
    expect(window.location.pathname).toBe("/script/16");
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });

  it("offers the pages of the episode in hand, and moves with the arrows", async () => {
    await act(async () => root.render(<Harness />));
    await press("k", window, { ctrlKey: true });
    await type("effects");

    const input = document.activeElement as HTMLInputElement;
    const first = input.getAttribute("aria-activedescendant");
    expect(host.querySelector(`#${CSS.escape(first!)}`)?.textContent).toContain("Effects");

    await press("Enter", input);
    expect(window.location.pathname).toBe("/script/17/effects");
  });

  it("matches names, not just the headings they sit under", async () => {
    await act(async () => root.render(<Harness />));
    await press("k", window, { metaKey: true });
    await type("01");
    const labels = [...host.querySelectorAll(".qs-label")].map((l) => l.textContent);
    expect(labels).toEqual(["011", "010"]);

    await type("011 effects");
    expect([...host.querySelectorAll(".qs-label")].map((l) => l.textContent)).toEqual(["Effects"]);
  });

  it("switches project from the list", async () => {
    await act(async () => root.render(<Harness />));
    await press("k", window, { metaKey: true });
    await type("wealth");
    await press("Enter", document.activeElement!);
    expect(onProject).toHaveBeenCalledWith(2);
  });

  it("closes on Escape and gives focus back", async () => {
    await act(async () => root.render(<Harness />));
    const before = host.querySelector("button") as HTMLButtonElement;
    before.focus();

    await press("k", window, { metaKey: true });
    await press("Escape", document.activeElement!);
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(before);
  });
});
