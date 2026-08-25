import { useEffect, useMemo, useState } from "react";
import { CheckIcon, DownloadIcon } from "../components/Icon";
import { Markdown, outlineOf } from "./Markdown";

/** A markdown document you can read, navigate, interrogate and take away.
 *
 *  Used for `plan.md` and for any `.md` or `.txt` artifact in the media
 *  library, which is the point of it: the editing plan is a document you want
 *  to *check* before committing to a download, and so is a script.
 */
export function DocumentView({
  source,
  filename,
  downloadUrl,
  onRowActivate,
  aside,
}: {
  source: string;
  /** Shown as the document's identity, and used for the download name. */
  filename: string;
  /** Where the real file lives. Absent for a document rendered from memory. */
  downloadUrl?: string;
  onRowActivate?: (cells: string[]) => void;
  /** An extra panel beside the outline — the plan uses it for its analysis. */
  aside?: React.ReactNode;
}) {
  const outline = useMemo(() => outlineOf(source), [source]);
  const [copied, setCopied] = useState(false);

  // A download for a document that has no file behind it — `plan.md` is
  // rendered from the endpoint, and until now the only way to get the file was
  // to run an export. The bytes are already here, so the browser can save them
  // directly; the object URL is revoked when the document changes so a long
  // session does not leak one per view.
  const href = useMemo(() => {
    if (downloadUrl) return downloadUrl;
    return URL.createObjectURL(new Blob([source], { type: "text/markdown;charset=utf-8" }));
  }, [source, downloadUrl]);

  useEffect(
    () => () => {
      if (!downloadUrl && href.startsWith("blob:")) URL.revokeObjectURL(href);
    },
    [href, downloadUrl],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused outright; the download is the fallback
      // and saying nothing is better than an alert.
    }
  }

  return (
    <div className="doc">
      <div className="doc-body">
        <div className="doc-head">
          <span className="mono faint">{filename}</span>
          <div className="toolbar">
            <button className="ghost small" onClick={copy} title="Copy the markdown">
              {copied ? <CheckIcon /> : null}
              {copied ? "Copied" : "Copy"}
            </button>
            <a className="download" href={href} download={filename}>
              <DownloadIcon /> {filename}
            </a>
          </div>
        </div>
        <Markdown source={source} onRowActivate={onRowActivate} />
      </div>

      <aside className="doc-side">
        {outline.length > 1 && (
          <nav className="doc-outline" aria-label="Document outline">
            <h3>Contents</h3>
            {outline.map((heading) => (
              <a
                key={heading.id}
                href={`#${heading.id}`}
                className={`doc-jump depth-${Math.min(heading.depth, 3)}`}
                onClick={(event) => {
                  // Scroll without pushing a history entry: the back button
                  // should leave the page, not undo a scroll.
                  event.preventDefault();
                  document
                    .getElementById(heading.id)
                    ?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {heading.text}
              </a>
            ))}
          </nav>
        )}
        {aside}
      </aside>
    </div>
  );
}
