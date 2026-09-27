// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelField } from "../components/ModelField";
import type { Model } from "../types";

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

function model(over: Partial<Model>): Model {
  return {
    model_id: "m",
    label: "M",
    usd_per_1k: 0.1,
    max_chars: 5000,
    request_stitching: false,
    continuity_mode: "none",
    audio_tags: true,
    settings_honoured: ["stability"],
    dialogue: false,
    dialogue_max_chars: 2000,
    long_form: "expressive",
    default: false,
    note: "",
    ...over,
  };
}

// In the order /api/models sends them: cheapest first, so the default is last
// and the model rated "best" for long form comes before it.
const MODELS = [
  model({ model_id: "eleven_flash_v2_5", label: "Flash v2.5", usd_per_1k: 0.05, long_form: "good" }),
  model({
    model_id: "eleven_multilingual_v2",
    label: "Multilingual v2",
    long_form: "best",
    request_stitching: true,
    continuity_mode: "request_ids",
  }),
  model({ model_id: "eleven_v3", label: "Eleven v3", default: true }),
];

describe("the model a new project starts on", () => {
  it("is the one the server marks as the default", async () => {
    const onChange = vi.fn();
    await act(async () => {
      root.render(<ModelField models={MODELS} value="" onChange={onChange} autoSelect />);
    });
    expect(onChange).toHaveBeenCalledWith("eleven_v3");
  });

  it("is labelled as the default in the list", async () => {
    await act(async () => {
      root.render(<ModelField models={MODELS} value="eleven_v3" onChange={() => {}} />);
    });
    const labels = [...host.querySelectorAll("option")].map((o) => o.textContent);
    expect(labels.filter((l) => l?.includes("(default)"))).toEqual([
      expect.stringContaining("Eleven v3"),
    ]);
  });

  it("never overrides a model already chosen", async () => {
    const onChange = vi.fn();
    await act(async () => {
      root.render(
        <ModelField models={MODELS} value="eleven_multilingual_v2" onChange={onChange} autoSelect />,
      );
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("a project on a model no longer offered", () => {
  it("shows that model, not whichever option comes first", async () => {
    await act(async () => {
      root.render(<ModelField models={MODELS} value="eleven_turbo_v2_5" onChange={() => {}} />);
    });
    const select = host.querySelector("select") as HTMLSelectElement;
    expect(select.value).toBe("eleven_turbo_v2_5");
    expect(select.options[select.selectedIndex].textContent).toContain("no longer offered");
  });
});
