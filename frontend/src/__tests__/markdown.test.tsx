// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Markdown, outlineOf, plain, sanitize } from "../ui/Markdown";

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

function render(source: string, onRowActivate?: (cells: string[]) => void) {
  act(() => root.render(<Markdown source={source} onRowActivate={onRowActivate} />));
}

/** A trimmed-down `plan.md`, the document this component exists for. */
const PLAN = `# The Keeper's Log — editing plan

Runtime **00:01:56.080** · 5 narration · 5 effects · spent $0.28 · $0.1462/min

## Running order

| # | Start | Length | Track | Content |
|---:|---|---:|---|---|
| 1 | 00:00:00.000 | 4.00s | **effect** | *distant foghorn* |
| 2 | 00:00:00.000 | 19.56s | narration | The light at Ardnamurchan… |
| 3 | 00:00:19.960 | 35.52s | narration | But something did go… |

## Effects used

- \`distant-foghorn-4s\` — placed at 00:00:00.000
`;

describe("rendering a plan", () => {
  it("makes the running order a real table", () => {
    render(PLAN);
    const table = host.querySelector("table");
    expect(table).not.toBeNull();
    expect(table!.querySelectorAll("thead th").length).toBe(5);
    expect(table!.querySelectorAll("tbody tr").length).toBe(3);
  });

  it("renders headings as headings, not as text with hashes", () => {
    render(PLAN);
    expect(host.querySelector("h1")?.textContent).toContain("The Keeper's Log");
    expect(host.querySelectorAll("h2").length).toBe(2);
    expect(host.textContent).not.toContain("# The Keeper's Log");
  });

  it("renders emphasis rather than showing asterisks", () => {
    render(PLAN);
    expect(host.querySelector("strong")?.textContent).toBe("00:01:56.080");
    expect(host.textContent).not.toContain("**");
  });

  it("renders a list as a list and code spans as code", () => {
    render(PLAN);
    expect(host.querySelectorAll("ul li").length).toBe(1);
    expect(host.querySelector("code")?.textContent).toBe("distant-foghorn-4s");
  });

  it("keeps column alignment from the markdown", () => {
    render(PLAN);
    const headers = [...host.querySelectorAll("thead th")].map((th) => th.className);
    expect(headers[0]).toBe("right");
    expect(headers[2]).toBe("right");
  });
});

describe("the table earns being a component", () => {
  it("sorts a column ascending, then descending, then back to source order", () => {
    render(PLAN);
    const lengths = () =>
      [...host.querySelectorAll("tbody tr")].map((tr) => tr.children[2]!.textContent!.trim());

    expect(lengths()).toEqual(["4.00s", "19.56s", "35.52s"]);

    const sortLength = host.querySelectorAll<HTMLButtonElement>(".md-sort")[2]!;
    act(() => sortLength.click());
    expect(lengths()).toEqual(["4.00s", "19.56s", "35.52s"]);

    act(() => sortLength.click());
    expect(lengths()).toEqual(["35.52s", "19.56s", "4.00s"]);

    act(() => sortLength.click());
    expect(lengths()).toEqual(["4.00s", "19.56s", "35.52s"]);
  });

  it("sorts figures as numbers, not as strings", () => {
    // The trap: "35.52s" sorts before "4.00s" alphabetically, and length is
    // exactly the column somebody sorts to find the longest chunk.
    render(PLAN);
    act(() => host.querySelectorAll<HTMLButtonElement>(".md-sort")[2]!.click());
    const first = host.querySelector("tbody tr")!.children[2]!.textContent!.trim();
    expect(first).toBe("4.00s");
  });

  it("sorts timecodes as times", () => {
    render(`| At |\n|---|\n| 00:09:00 |\n| 00:01:40 |\n| 00:10:00 |\n`);
    act(() => host.querySelector<HTMLButtonElement>(".md-sort")!.click());
    const order = [...host.querySelectorAll("tbody tr")].map((tr) => tr.textContent!.trim());
    expect(order).toEqual(["00:01:40", "00:09:00", "00:10:00"]);
  });

  it("reports a clicked row as plain text, markdown stripped", () => {
    const onRow = vi.fn();
    render(PLAN, onRow);
    act(() => host.querySelectorAll<HTMLElement>("tbody tr")[0]!.click());
    expect(onRow).toHaveBeenCalledWith([
      "1",
      "00:00:00.000",
      "4.00s",
      "effect",
      "distant foghorn",
    ]);
  });

  it("marks the sorted column for assistive technology", () => {
    render(PLAN);
    act(() => host.querySelectorAll<HTMLButtonElement>(".md-sort")[2]!.click());
    const sorted = host.querySelectorAll("thead th")[2]!;
    expect(sorted.getAttribute("aria-sort")).toBe("ascending");
  });
});

describe("a document can be navigated", () => {
  it("builds an outline from the headings", () => {
    expect(outlineOf(PLAN)).toEqual([
      { depth: 1, text: "The Keeper's Log — editing plan", id: "the-keeper-s-log-editing-plan" },
      { depth: 2, text: "Running order", id: "running-order" },
      { depth: 2, text: "Effects used", id: "effects-used" },
    ]);
  });

  it("gives repeated headings distinct anchors", () => {
    const outline = outlineOf("## Notes\n\ntext\n\n## Notes\n\nmore\n");
    expect(outline.map((h) => h.id)).toEqual(["notes", "notes-2"]);
  });

  it("puts the anchor id on the rendered heading", () => {
    render(PLAN);
    expect(host.querySelector("#running-order")?.tagName).toBe("H2");
  });
});

describe("markdown from anywhere is inert", () => {
  it("drops a script tag", () => {
    render(`Before <script>window.__pwned = 1</script> after`);
    expect(host.querySelector("script")).toBeNull();
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it("drops an inline event handler", () => {
    render(`![x](data:image/gif;base64,R0lGODlhAQABAAAAACw=)\n\n<img src=x onerror="window.__pwned=1">`);
    for (const img of host.querySelectorAll("img")) {
      expect(img.getAttribute("onerror")).toBeNull();
    }
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it("drops a javascript: link", () => {
    render(`[click](javascript:window.__pwned=1)`);
    const href = host.querySelector("a")?.getAttribute("href") ?? "";
    expect(href.startsWith("javascript:")).toBe(false);
  });

  it("drops an iframe and a form", () => {
    render(`<iframe src="https://example.test"></iframe>\n\n<form><input name="p"></form>`);
    expect(host.querySelector("iframe")).toBeNull();
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector("input")).toBeNull();
  });

  it("sanitises inside a table cell too", () => {
    render(`| A |\n|---|\n| <img src=x onerror="window.__pwned=1"> |\n`);
    expect(host.querySelector("td img")?.getAttribute("onerror")).toBeNull();
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });
});

describe("helpers", () => {
  it("strips markdown down to its words", () => {
    expect(plain("**bold** and `code` and [a link](http://x)")).toBe("bold and code and a link");
  });

  it("sanitize leaves ordinary formatting alone", () => {
    expect(sanitize("<em>fine</em>")).toBe("<em>fine</em>");
  });

  it("renders an empty document without failing", () => {
    render("");
    expect(host.querySelector(".md")).not.toBeNull();
  });
});
