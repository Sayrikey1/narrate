// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CastPage } from "../pages/Cast";
import type { CastMember, Project, Voice } from "../types";

const members: CastMember[] = [{ id: 4, name: "Morag", voice_id: "v-george", note: "" }];
const removeCastMember = vi.fn(async () => ({}));
const setCastMember = vi.fn(async () => ({}));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  api: {
    cast: () => Promise.resolve(members),
    voices: () => Promise.resolve(VOICES),
    voicePreview: (id: string) => `/api/voices/${id}/preview`,
    removeCastMember: (...args: unknown[]) => removeCastMember(...(args as [])),
    setCastMember: (...args: unknown[]) => setCastMember(...(args as [])),
  },
}));

const VOICES: Voice[] = [
  { voice_id: "v-rachel", name: "Rachel", category: "premade", labels: { accent: "american" }, description: null, preview: true, settings: null, mock: false },
  { voice_id: "v-george", name: "George - Warm Storyteller", category: "premade", labels: { accent: "british" }, description: null, preview: true, settings: null, mock: false },
];

const project: Project = {
  id: 1,
  name: "HINDSIGHT",
  voice_id: "v-rachel",
  model_id: "eleven_v3",
  spend_micros: 0,
  waste_pct: 0,
  month_micros: 0,
  cap_micros: null,
  cap_used_pct: 0,
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  removeCastMember.mockClear();
  setCastMember.mockClear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const render = async () => {
  await act(async () => {
    root.render(<CastPage project={project} models={[]} voices={VOICES} scripts={[]} onChanged={vi.fn()} />);
  });
};

const button = (label: string) =>
  [...host.querySelectorAll("button")].find(
    (b) => b.textContent?.includes(label) || b.getAttribute("aria-label")?.includes(label),
  );

describe("the cast", () => {
  it("shows the narrator and each speaker with their voice", async () => {
    await render();
    expect(host.textContent).toContain("Narrator");
    expect(host.textContent).toContain("Rachel");
    expect(host.textContent).toContain("Morag:");
    // The name without the blurb.
    expect(host.textContent).toContain("George");
  });

  it("asks before removing a speaker, and says what happens to their lines", async () => {
    await render();
    await act(async () => button("Remove Morag from the cast")?.click());
    const dialog = host.querySelector('[role="alertdialog"]');
    expect(dialog?.textContent).toContain("will be read by the narrator");

    await act(async () => button("Keep")?.click());
    expect(host.querySelector('[role="alertdialog"]')).toBeNull();
    expect(removeCastMember).not.toHaveBeenCalled();

    await act(async () => button("Remove Morag from the cast")?.click());
    const remove = [...host.querySelectorAll('[role="alertdialog"] button')].find((b) => b.textContent === "Remove");
    await act(async () => (remove as HTMLButtonElement).click());
    expect(removeCastMember).toHaveBeenCalledWith(1, 4);
  });

  it("recasts a speaker in another voice through the same form", async () => {
    await render();
    await act(async () => button("Change voice…")?.click());
    expect(host.textContent).toContain("Recast Morag");

    const rachel = [...host.querySelectorAll('[role="radio"]')].find((r) => r.textContent?.includes("Rachel"));
    await act(async () => (rachel as HTMLButtonElement).click());
    await act(async () => button("Recast Morag as Rachel")?.click());

    expect(setCastMember).toHaveBeenCalledWith(1, { name: "Morag", voice_id: "v-rachel", note: "" });
  });
});
