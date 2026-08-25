// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoicePicker } from "../components/VoicePicker";
import type { Voice } from "../types";

/** Two voices, one with a sample and one without. */
const VOICES: Voice[] = [
  {
    voice_id: "v-rachel",
    name: "Rachel",
    category: "premade",
    labels: { accent: "american" },
    description: null,
    preview: true,
    settings: null,
    mock: false,
  },
  {
    voice_id: "v-adam",
    name: "Adam",
    category: "premade",
    labels: { accent: "british" },
    description: null,
    preview: false,
    settings: null,
    mock: false,
  },
];

vi.mock("../api", () => ({
  api: {
    voices: () => Promise.resolve(VOICES),
    voicePreview: (id: string) => `/api/voices/${id}/preview`,
  },
}));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  // jsdom has no media stack; the audition only ever constructs this lazily.
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function mount(onVoice = vi.fn(), voiceId: string | null = null) {
  await act(async () => {
    root.render(
      <VoicePicker
        model={undefined}
        voiceId={voiceId}
        settings={{}}
        onVoice={onVoice}
        onSettings={vi.fn()}
      />,
    );
  });
  return onVoice;
}

describe("the structure that broke auditioning", () => {
  it("never puts two labelable controls in one label", async () => {
    // This is the bug, as an invariant. Each row used to be a <label> wrapping
    // both a radio and a play button. `button` is a *labelable* element, so
    // that label had two labelable descendants — invalid HTML, and browsers
    // disagree about what a click inside one means: it was retargeted to the
    // radio, so playing a sample also selected the voice.
    //
    // The rule is "at most one", not "none": `<label><input
    // type="checkbox">Speaker boost</label>` is correct, idiomatic HTML and
    // this component uses it. Banning it outright would make the guard fail on
    // perfectly good markup and get deleted.
    await mount();
    for (const label of host.querySelectorAll("label")) {
      const labelable = label.querySelectorAll(
        "button, input, meter, output, progress, select, textarea",
      );
      expect(labelable.length).toBeLessThanOrEqual(1);
      // A link inside a label is the same class of mistake: the label swallows
      // the click. `Player` used to render an <a download> in there.
      expect(label.querySelector("a")).toBeNull();
    }
  });

  it("exposes the list as a radiogroup of radios", async () => {
    await mount();
    expect(host.querySelector('[role="radiogroup"]')).not.toBeNull();
    expect(host.querySelectorAll('[role="radio"]').length).toBe(2);
  });

  it("is one tab stop, not one per voice", async () => {
    await mount(vi.fn(), "v-adam");
    const focusable = [...host.querySelectorAll<HTMLElement>('[role="radio"]')].filter(
      (el) => el.tabIndex === 0,
    );
    expect(focusable.length).toBe(1);
    expect(focusable[0]!.textContent).toContain("Adam");
  });
});

describe("auditioning and selecting are separate acts", () => {
  it("hearing a voice does not select it", async () => {
    const onVoice = await mount();
    const audition = host.querySelector<HTMLButtonElement>(".audition")!;

    await act(async () => audition.click());

    // The whole complaint: previewing used to select.
    expect(onVoice).not.toHaveBeenCalled();
  });

  it("selecting a voice reports it once", async () => {
    const onVoice = await mount();
    const rows = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');

    await act(async () => rows[1]!.click());

    expect(onVoice).toHaveBeenCalledTimes(1);
    expect(onVoice).toHaveBeenCalledWith("v-adam");
  });

  it("only offers an audition for a voice that has a sample", async () => {
    await mount();
    expect(host.querySelectorAll(".audition").length).toBe(1);
    expect(host.textContent).toContain("no sample");
  });

  it("plays the proxied url, never the provider's own link", async () => {
    await mount();
    const audition = host.querySelector<HTMLButtonElement>(".audition")!;
    expect(audition.getAttribute("aria-label")).toContain("Rachel");
    await act(async () => audition.click());
    // Constructed lazily on click, so a hundred voices are not a hundred fetches.
    expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });
});

describe("keyboard movement", () => {
  it("arrow keys move the selection", async () => {
    const onVoice = await mount(vi.fn(), "v-rachel");
    const rows = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');

    await act(async () => {
      rows[0]!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
      );
    });

    expect(onVoice).toHaveBeenCalledWith("v-adam");
  });

  it("wraps from the end back to the start", async () => {
    const onVoice = await mount(vi.fn(), "v-adam");
    const rows = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');

    await act(async () => {
      rows[1]!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
      );
    });

    expect(onVoice).toHaveBeenCalledWith("v-rachel");
  });
});
