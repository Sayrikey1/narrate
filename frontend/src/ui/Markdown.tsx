import DOMPurify from "dompurify";
import { marked, type Token, type Tokens } from "marked";
import { useMemo, useState } from "react";

/** Markdown as a document, not a `<pre>`.
 *
 *  `plan.md` is the reason this exists. It is the editing document that ships
 *  with every export, and its running order is a table — which as preformatted
 *  text arrives as a wall of pipes and dashes, unreadable exactly where it
 *  matters most.
 *
 *  **Block-level tokens become React elements; only inline runs go through
 *  HTML.** Rendering the whole document with `innerHTML` would be four lines
 *  instead of these, but a table built that way is inert: no sorting, no
 *  sticky header, no clicking a row to hear that part of the timeline. Blocks
 *  are where the interesting behaviour lives, so blocks are components.
 *
 *  Inline content — emphasis, code spans, links — is parsed by `marked` and
 *  then **always** passed through DOMPurify before it reaches the DOM. A
 *  script may be uploaded from anywhere, so `<img src=x onerror=…>` in a `.md`
 *  file is a real input, not a hypothetical one.
 */

export interface MarkdownProps {
  source: string;
  /** Called when a table row is clicked, with its cells as plain text.
   *  The plan's running order uses this to seek the transport. */
  onRowActivate?: (cells: string[]) => void;
  /** Extra classes on the wrapper. */
  className?: string;
}

/** Anything with a heading, so a document can grow a table of contents. */
export interface Outline {
  depth: number;
  text: string;
  id: string;
}

export function outlineOf(source: string): Outline[] {
  const seen = new Map<string, number>();
  return marked
    .lexer(source)
    .filter((t): t is Tokens.Heading => t.type === "heading")
    .map((token) => {
      const text = plain(token.text);
      const base = slug(text);
      // Two identical headings must not produce two identical anchors, or the
      // second link jumps to the first section.
      const count = (seen.get(base) ?? 0) + 1;
      seen.set(base, count);
      return { depth: token.depth, text, id: count > 1 ? `${base}-${count}` : base };
    });
}

export function Markdown({ source, onRowActivate, className }: MarkdownProps) {
  const tokens = useMemo(() => marked.lexer(source), [source]);
  const ids = useMemo(() => new Map(outlineOf(source).map((h) => [h.text, h.id])), [source]);

  return (
    <div className={["md", className].filter(Boolean).join(" ")}>
      {tokens.map((token, index) => (
        <Block key={index} token={token} ids={ids} onRowActivate={onRowActivate} />
      ))}
    </div>
  );
}

function Block({
  token,
  ids,
  onRowActivate,
}: {
  token: Token;
  ids: Map<string, string>;
  onRowActivate?: (cells: string[]) => void;
}) {
  switch (token.type) {
    case "heading": {
      const heading = token as Tokens.Heading;
      const text = plain(heading.text);
      const Tag = `h${Math.min(6, heading.depth)}` as "h1";
      return (
        <Tag id={ids.get(text)} className="md-h">
          <Inline text={heading.text} />
        </Tag>
      );
    }

    case "paragraph":
      return (
        <p className="md-p">
          <Inline text={(token as Tokens.Paragraph).text} />
        </p>
      );

    case "table":
      return <MarkdownTable token={token as Tokens.Table} onRowActivate={onRowActivate} />;

    case "list": {
      const list = token as Tokens.List;
      const Tag = list.ordered ? "ol" : "ul";
      return (
        <Tag className="md-list" start={list.ordered ? Number(list.start || 1) : undefined}>
          {list.items.map((item, i) => (
            <li key={i}>
              {item.task && <input type="checkbox" checked={item.checked} disabled readOnly />}
              <Inline text={item.text} />
            </li>
          ))}
        </Tag>
      );
    }

    case "code": {
      const code = token as Tokens.Code;
      return (
        <pre className="md-code">
          <code>{code.text}</code>
        </pre>
      );
    }

    case "blockquote":
      return (
        <blockquote className="md-quote">
          <Inline text={(token as Tokens.Blockquote).text} />
        </blockquote>
      );

    case "hr":
      return <hr className="md-hr" />;

    case "html":
      // Block-level raw HTML. Sanitised like everything else — a `.md` can
      // arrive from anywhere.
      return <Inline text={(token as Tokens.HTML).text} block />;

    case "space":
      return null;

    default:
      // `text`, and anything a future `marked` adds. Rendering it as inline
      // content is the safe default: worst case it looks plain, and it still
      // cannot inject.
      return <Inline text={"text" in token ? String(token.text) : token.raw} />;
  }
}

/** Inline markdown, parsed then sanitised. The only place this file touches
 *  `innerHTML`, and it never does so without DOMPurify in between. */
function Inline({ text, block }: { text: string; block?: boolean }) {
  const html = useMemo(() => sanitize(block ? text : marked.parseInline(text) as string), [text, block]);
  const Tag = block ? "div" : "span";
  return <Tag className="md-inline" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** One sanitiser configuration for the whole app.
 *
 *  `FORBID_TAGS`/`FORBID_ATTR` are belt-and-braces: DOMPurify already strips
 *  scripts and event handlers by default, and stating the intent here means a
 *  future config change cannot silently widen it. `target="_blank"` without
 *  `rel="noopener"` is a real leak, so links are hardened on the way out.
 */
export function sanitize(html: string): string {
  const clean = DOMPurify.sanitize(html, {
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "input", "button"],
    FORBID_ATTR: ["style", "srcset", "formaction", "onerror", "onload"],
    ALLOW_DATA_ATTR: false,
  });
  return clean;
}

/** Markdown stripped to its words — for anchors, titles and sort keys. */
export function plain(text: string): string {
  const div = document.createElement("div");
  div.innerHTML = sanitize(marked.parseInline(text) as string);
  return (div.textContent || "").trim();
}

function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "section"
  );
}

// ---------------------------------------------------------------------------
// Tables — the reason blocks are components rather than HTML
// ---------------------------------------------------------------------------

function MarkdownTable({
  token,
  onRowActivate,
}: {
  token: Tokens.Table;
  onRowActivate?: (cells: string[]) => void;
}) {
  const [sort, setSort] = useState<{ column: number; desc: boolean } | null>(null);

  const rows = useMemo(
    () => token.rows.map((cells) => cells.map((cell) => ({ md: cell.text, text: plain(cell.text) }))),
    [token],
  );

  const ordered = useMemo(() => {
    if (!sort) return rows;
    const { column, desc } = sort;
    // A stable copy: mutating `rows` would reorder the memo the next render
    // reads from, so a second sort would compound rather than replace.
    return [...rows].sort((a, b) => {
      const left = a[column]?.text ?? "";
      const right = b[column]?.text ?? "";
      return (desc ? -1 : 1) * compare(left, right);
    });
  }, [rows, sort]);

  return (
    <div className="md-table-scroll">
      <table className="md-table">
        <thead>
          <tr>
            {token.header.map((cell, index) => {
              const active = sort?.column === index;
              return (
                <th key={index} className={token.align[index] ?? undefined} aria-sort={
                  active ? (sort.desc ? "descending" : "ascending") : "none"
                }>
                  <button
                    type="button"
                    className="md-sort"
                    onClick={() =>
                      setSort((s) =>
                        s?.column === index ? (s.desc ? null : { column: index, desc: true }) : { column: index, desc: false },
                      )
                    }
                    title="Sort by this column"
                  >
                    <Inline text={cell.text} />
                    <span className="md-sort-mark" aria-hidden>
                      {active ? (sort.desc ? "↓" : "↑") : ""}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {ordered.map((cells, rowIndex) => (
            <tr
              key={rowIndex}
              className={onRowActivate ? "md-row-action" : undefined}
              onClick={onRowActivate ? () => onRowActivate(cells.map((c) => c.text)) : undefined}
            >
              {cells.map((cell, index) => (
                <td key={index} className={token.align[index] ?? undefined}>
                  <Inline text={cell.md} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Numbers and timecodes sort as values, everything else as text.
 *
 *  Without this the running order sorts `00:01:40` before `00:09:00` correctly
 *  by luck, but `9.5s` before `35.52s` wrongly — and length is exactly the
 *  column somebody sorts to find the longest chunk. */
function compare(a: string, b: string): number {
  const left = numeric(a);
  const right = numeric(b);
  if (left !== null && right !== null) return left - right;
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function numeric(value: string): number | null {
  const clock = /^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$/.exec(value.trim());
  if (clock) {
    const [, a, b, c] = clock;
    return c === undefined
      ? Number(a) * 60 + Number(b)
      : Number(a) * 3600 + Number(b) * 60 + Number(c);
  }
  // `$0.0346`, `35.52s`, `1,928` — a figure wearing a unit.
  const bare = value.trim().replace(/[$,]/g, "").replace(/(s|ms|cr|%)$/i, "");
  if (bare && /^-?\d+(\.\d+)?$/.test(bare)) return Number(bare);
  return null;
}
