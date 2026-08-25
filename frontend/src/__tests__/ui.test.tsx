// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Empty, Loadable, Notice, Skeleton, Stat } from "../ui/feedback";
import { TextField } from "../ui/form";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import { Tabs } from "../ui/Tabs";

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

const render = (node: React.ReactNode) => act(() => root.render(node));

/** Type into a controlled input the way React expects.
 *
 *  React tracks an input's value on the node, so assigning `.value` and firing
 *  `input` looks like no change at all and `onChange` never runs. Going through
 *  the prototype's setter is what makes React see it. */
function type(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

interface Row {
  id: number;
  name: string;
  cost: number;
}

const COLUMNS: readonly Column<Row>[] = [
  { header: "Name", cell: (r) => r.name },
  { header: "Cost", align: "num", cell: (r) => r.cost.toFixed(2) },
];

describe("the three states of a fetch", () => {
  it("shows a skeleton while loading, not an empty state", () => {
    // The distinction that was missing: a page still loading looked identical
    // to a page with nothing in it, which reads as "there is nothing here".
    render(<Loadable loading>{<p>content</p>}</Loadable>);
    expect(host.querySelector(".skeleton")).not.toBeNull();
    expect(host.textContent).not.toContain("content");
  });

  it("announces loading to a screen reader", () => {
    render(<Loadable loading>{null}</Loadable>);
    const live = host.querySelector('[role="status"]');
    expect(live?.getAttribute("aria-live")).toBe("polite");
    // The grey rectangles themselves are noise to a reader.
    expect(host.querySelector(".skeleton")?.getAttribute("aria-hidden")).not.toBeNull();
  });

  it("shows the error rather than swallowing it", () => {
    render(<Loadable loading={false} error="HTTP 500">{<p>content</p>}</Loadable>);
    expect(host.textContent).toContain("HTTP 500");
    expect(host.textContent).not.toContain("content");
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
  });

  it("offers a retry when one is given", () => {
    const onRetry = vi.fn();
    render(<Loadable loading={false} error="nope" onRetry={onRetry}>{null}</Loadable>);
    act(() => host.querySelector<HTMLButtonElement>("button")!.click());
    expect(onRetry).toHaveBeenCalled();
  });

  it("renders the content once loaded", () => {
    render(<Loadable loading={false}>{<p>content</p>}</Loadable>);
    expect(host.textContent).toContain("content");
    expect(host.querySelector(".skeleton")).toBeNull();
  });
});

describe("tables", () => {
  it("right-aligns a numeric column in the header and the body", () => {
    // Declared once per column rather than remembered per cell, which is how
    // a column of costs ends up half-aligned.
    render(
      <DataTable
        columns={COLUMNS}
        rows={[{ id: 1, name: "Ep14", cost: 0.28 }]}
        rowKey={(r) => r.id}
      />,
    );
    expect(host.querySelectorAll("th")[1]!.className).toContain("num");
    expect(host.querySelectorAll("td")[1]!.className).toContain("num");
  });

  it("shows an empty state with a reason instead of a blank body", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        empty={{ title: "No projects yet", body: "Spend appears here once something runs." }}
      />,
    );
    expect(host.querySelector("table")).toBeNull();
    expect(host.textContent).toContain("No projects yet");
    expect(host.textContent).toContain("Spend appears here");
  });

  it("renders rows when there are any, empty state or not", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={[{ id: 1, name: "A", cost: 1 }, { id: 2, name: "B", cost: 2 }]}
        rowKey={(r) => r.id}
        empty={{ title: "nothing" }}
      />,
    );
    expect(host.querySelectorAll("tbody tr").length).toBe(2);
  });

  it("keeps a wide table inside its own scroll box", () => {
    // Otherwise the whole page scrolls sideways and the nav goes with it.
    render(<DataTable columns={COLUMNS} rows={[{ id: 1, name: "A", cost: 1 }]} rowKey={(r) => r.id} />);
    expect(host.querySelector(".table-scroll")).not.toBeNull();
  });

  it("reports a clicked row", () => {
    const onRowClick = vi.fn();
    const row = { id: 7, name: "G", cost: 3 };
    render(<DataTable columns={COLUMNS} rows={[row]} rowKey={(r) => r.id} onRowClick={onRowClick} />);
    act(() => host.querySelector<HTMLElement>("tbody tr")!.click());
    expect(onRowClick).toHaveBeenCalledWith(row);
  });
});

describe("tabs", () => {
  const TABS = [
    { id: "a" as const, label: "Document" },
    { id: "b" as const, label: "Analysis" },
  ];

  it("is one tab stop, not one per tab", () => {
    render(<Tabs tabs={TABS} active="a" onChange={vi.fn()} label="View" />);
    const stops = [...host.querySelectorAll<HTMLElement>('[role="tab"]')].filter(
      (el) => el.tabIndex === 0,
    );
    expect(stops.length).toBe(1);
  });

  it("associates the panel with its tab", () => {
    render(<Tabs tabs={TABS} active="a" onChange={vi.fn()} label="View" />);
    const tab = host.querySelector('[role="tab"][aria-selected="true"]')!;
    expect(tab.getAttribute("aria-controls")).toBe("panel-a");
  });

  it("moves with the arrow keys and wraps", () => {
    const onChange = vi.fn();
    render(<Tabs tabs={TABS} active="a" onChange={onChange} label="View" />);
    const tabs = host.querySelectorAll<HTMLElement>('[role="tab"]');

    act(() => {
      tabs[0]!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    expect(onChange).toHaveBeenCalledWith("b");

    act(() => {
      tabs[0]!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith("b");
  });
});

describe("fields", () => {
  it("associates its label with its control", () => {
    // Most fields were an adjacent <label> and <input> — clicking the label did
    // nothing and a screen reader announced an unlabelled box.
    render(<TextField label="Name" value="" onChange={vi.fn()} />);
    const label = host.querySelector("label")!;
    const input = host.querySelector("input")!;
    expect(label.getAttribute("for")).toBe(input.id);
    expect(input.id).not.toBe("");
  });

  it("reports an error as an alert", () => {
    render(<TextField label="Name" value="" onChange={vi.fn()} error="Required" />);
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("Required");
  });

  it("passes the typed value up", () => {
    const onChange = vi.fn();
    render(<TextField label="Name" value="" onChange={onChange} />);
    const input = host.querySelector<HTMLInputElement>("input")!;
    act(() => type(input, "Ep14"));
    expect(onChange).toHaveBeenCalledWith("Ep14");
  });
});

describe("layout and messages", () => {
  it("a page header carries its title and actions", () => {
    render(<PageHeader title="Costs" subtitle="every dollar" actions={<button>Go</button>} />);
    expect(host.querySelector("h1")?.textContent).toBe("Costs");
    expect(host.querySelector(".toolbar button")?.textContent).toBe("Go");
  });

  it("a card without a title renders no empty heading", () => {
    render(<Card>{<p>body</p>}</Card>);
    expect(host.querySelector("h2")).toBeNull();
    expect(host.querySelector(".panel")?.textContent).toBe("body");
  });

  it("a split renders the aside only when given one", () => {
    render(<Split main={<p>main</p>} />);
    expect(host.querySelector(".side")).toBeNull();
    render(<Split main={<p>main</p>} aside={<p>aside</p>} />);
    expect(host.querySelector(".side")?.textContent).toBe("aside");
  });

  it("an empty state can offer the action that would fill it", () => {
    render(<Empty title="No projects" action={<button>New</button>}>Add one.</Empty>);
    expect(host.textContent).toContain("Add one.");
    expect(host.querySelector("button")?.textContent).toBe("New");
  });

  it("a notice carries its tone as a class", () => {
    render(<Notice tone="warn">careful</Notice>);
    expect(host.querySelector(".notice")?.className).toContain("warn");
  });

  it("only an error notice is an alert", () => {
    render(<Notice tone="info">fine</Notice>);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it("a stat pairs a label with a figure", () => {
    render(<Stat label="Spent" value="$0.28" total />);
    expect(host.querySelector(".stat")?.className).toContain("total");
    expect(host.textContent).toContain("$0.28");
  });

  it("a skeleton renders the rows asked for", () => {
    render(<Skeleton rows={5} />);
    expect(host.querySelectorAll(".skeleton-row").length).toBe(5);
  });
});
