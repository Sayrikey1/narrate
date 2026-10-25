// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { niceTop, SpendPerDay } from "../components/SpendPerDay";
import type { Project, Spend } from "../types";

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

const spend: Spend = {
  month_start: "2026-10-01",
  month_micros: 1_450_000,
  last_month_start: "2026-09-01",
  last_month_micros: 900_000,
  days: [
    { date: "2026-10-01", micros: 0, by_project: {} },
    { date: "2026-10-02", micros: 1_200_000, by_project: { "5": 1_000_000, "1": 200_000 } },
    { date: "2026-10-03", micros: 250_000, by_project: { "5": 250_000 } },
  ],
};
const projects = [
  { id: 5, name: "HINDSIGHT" },
  { id: 1, name: "Ep14" },
] as Project[];

describe("spend per day", () => {
  it("rounds the scale up to a clean figure", () => {
    expect(niceTop(1_200_000)).toBe(2_000_000);
    expect(niceTop(340_000)).toBe(500_000);
    expect(niceTop(50_000)).toBe(50_000);
    expect(niceTop(0)).toBe(1_000_000);
  });

  it("labels only the highest day, and tells each day's figure on focus", async () => {
    await act(async () => root.render(<SpendPerDay spend={spend} projects={projects} />));

    expect([...host.querySelectorAll(".daily-peak")].map((p) => p.textContent)).toEqual(["$1.20"]);
    const day = host.querySelector<HTMLButtonElement>('button[aria-label^="Fri 2 Oct"]')!;
    expect(day.getAttribute("aria-label")).toBe("Fri 2 Oct: $1.20");

    await act(async () => day.focus());
    const tip = host.querySelector('[role="status"]');
    expect(tip?.textContent).toContain("$1.20");
    expect(tip?.textContent).toContain("HINDSIGHT $1.00 · Ep14 $0.20");
  });

  it("keeps every figure in a table too", async () => {
    await act(async () => root.render(<SpendPerDay spend={spend} projects={projects} />));
    expect(host.querySelector(".daily-table")?.textContent).toContain("Sat 3 Oct · $0.25");
  });
});
