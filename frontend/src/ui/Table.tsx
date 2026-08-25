import type { ReactNode } from "react";
import { Empty } from "./feedback";

/** A data table with its columns declared rather than hand-written.
 *
 *  Every table in this app is numbers in columns, and each one had spelled out
 *  its own `<thead>`/`<tbody>` — which meant each one decided separately
 *  whether figures were right-aligned, whether there was an empty state, and
 *  whether the header stayed put while the body scrolled. Some did none of it.
 *
 *  Declaring the columns puts those decisions in one place: `align: "num"`
 *  right-aligns *and* applies tabular figures, so a column of costs lines up on
 *  the decimal point without anyone having to remember.
 */

export interface Column<Row> {
  /** Header text. Empty string for an actions column. */
  header: ReactNode;
  /** How to render a cell. */
  cell: (row: Row, index: number) => ReactNode;
  /** `num` right-aligns and uses tabular figures. */
  align?: "left" | "num" | "center";
  /** Stops a long cell from squeezing the rest. */
  width?: string;
  className?: string;
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  empty,
  onRowClick,
  compact,
  caption,
}: {
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row, index: number) => string | number;
  /** Shown instead of an empty body. A table with a blank body reads as
   *  broken; a sentence saying what would fill it does not. */
  empty?: { title: string; body?: ReactNode; action?: ReactNode };
  onRowClick?: (row: Row) => void;
  compact?: boolean;
  /** Describes the table for assistive technology; visually hidden. */
  caption?: string;
}) {
  if (!rows.length && empty) {
    return <Empty title={empty.title} action={empty.action}>{empty.body}</Empty>;
  }

  return (
    <div className="table-scroll">
      <table className={compact ? "table-compact" : undefined}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column, i) => (
              <th
                key={i}
                className={cellClass(column)}
                style={column.width ? { width: column.width } : undefined}
                scope="col"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={rowKey(row, index)}
              className={onRowClick ? "row-action" : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((column, i) => (
                <td key={i} className={cellClass(column)}>
                  {column.cell(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cellClass<Row>(column: Column<Row>): string | undefined {
  const parts = [column.className];
  if (column.align === "num") parts.push("num");
  if (column.align === "center") parts.push("center");
  return parts.filter(Boolean).join(" ") || undefined;
}
