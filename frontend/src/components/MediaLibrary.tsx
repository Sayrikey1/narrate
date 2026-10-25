import { useId, useState } from "react";
import type { ReactNode } from "react";
import { api, bytes } from "../api";
import { day, READABLE, type Entry } from "../mediaFiles";
import { Link, scriptPath } from "../router";
import { DocumentView } from "../ui/DocumentView";
import { ChevronDownIcon, DownloadIcon } from "./Icon";
import { Player } from "./Player";
import type { MediaFile, SavedArchive, ScriptSummary } from "../types";

/** How many of a long list show before "Show N more" — enough to see what is
 *  there without a hundred players loading their audio at once. */
const FIRST_PIECES = 3;
const FIRST_TAKES = 6;

/** Everything a project has produced, grouped by episode: each export's
 *  masters, pieces and documents, every take, and the effect library.
 *
 *  Groups open and close; a closed one renders nothing inside it, so the whole
 *  project can be listed without loading every player in it. */
export function MediaLibrary({
  entries,
  scripts,
  currentId,
  wholeProject,
  exportFolder,
  searching,
}: {
  /** Already scoped, searched, filtered and sorted. */
  entries: Entry[];
  /** The project's episodes, for their titles and their order. */
  scripts: ScriptSummary[];
  /** The episode in context: listed first, and open. */
  currentId: number | null;
  wholeProject: boolean;
  exportFolder: string | null;
  /** A search shows every match rather than the first few of each list. */
  searching: boolean;
}) {
  const current = scripts.find((s) => s.id === currentId);
  const order = [
    ...(current ? [current] : []),
    ...[...scripts].reverse().filter((s) => s.id !== currentId),
  ];
  const effects = entries.filter((e) => e.kind === "effect");

  return (
    <div className="media-groups">
      {order.flatMap((script) => {
        const own = entries.filter((e) => e.scriptId === script.id);
        const exported = own.filter((e) => e.kind !== "take");
        const takes = own.filter((e) => e.kind === "take");
        const open = !wholeProject || script.id === currentId || searching;
        return [
          exported.length > 0 && (
            <ExportGroup
              key={`export-${script.id}`}
              script={script}
              entries={exported}
              open={open}
              exportFolder={exportFolder}
              searching={searching}
            />
          ),
          takes.length > 0 && (
            <Collapsible
              key={`takes-${script.id}`}
              icon="🎙️"
              title={`Takes for ${script.title}`}
              meta={
                <>
                  {takes.length} take{takes.length === 1 ? "" : "s"} ·{" "}
                  <span className="mono">{bytes(total(takes))}</span> · every one is kept, even the
                  ones not in the cut
                </>
              }
              open={open}
            >
              <Rows entries={takes} first={searching ? Infinity : FIRST_TAKES} noun="take" />
            </Collapsible>
          ),
        ];
      })}

      {effects.length > 0 && (
        <Collapsible
          icon="✨"
          title={wholeProject || !current ? "Effect library" : `Effects used in ${current.title}`}
          meta={
            <>
              {effects.length} sound{effects.length === 1 ? "" : "s"} ·{" "}
              <span className="mono">{bytes(total(effects))}</span> · from the project's effect
              library
            </>
          }
          open={!wholeProject || searching}
        >
          <Rows entries={effects} first={Infinity} noun="effect" />
        </Collapsible>
      )}
    </div>
  );
}

function ExportGroup({
  script,
  entries,
  open,
  exportFolder,
  searching,
}: {
  script: ScriptSummary;
  entries: Entry[];
  open: boolean;
  exportFolder: string | null;
  searching: boolean;
}) {
  const masters = entries.filter((e) => e.kind === "master");
  const pieces = entries.filter((e) => e.kind === "piece");
  const documents = entries.filter((e) => e.kind === "document");
  const newest = Math.max(...entries.map((e) => e.file.modified));

  return (
    <Collapsible
      icon="📦"
      title={`${script.title} — export`}
      meta={
        <>
          {day(newest, true)} · {entries.length} file{entries.length === 1 ? "" : "s"} ·{" "}
          <span className="mono">{bytes(total(entries))}</span>
        </>
      }
      action={<ZipAction scriptId={script.id} title={script.title} folder={exportFolder} />}
      open={open}
    >
      {masters.length > 0 && (
        <div className="media-section">
          <span className="media-section-label">🎧 Masters</span>
          <Rows entries={masters} first={Infinity} noun="master" />
        </div>
      )}
      {pieces.length > 0 && (
        <Collapsible
          nested
          icon="🧩"
          title={`${pieces.length} piece${pieces.length === 1 ? "" : "s"}`}
          meta="— every take and effect as its own file, named by where it sits, ready to drop on a timeline"
          open
        >
          <Rows entries={pieces} first={searching ? Infinity : FIRST_PIECES} noun="piece" />
        </Collapsible>
      )}
      {documents.length > 0 && <Documents scriptId={script.id} entries={documents} />}
    </Collapsible>
  );
}

/** A heading that opens and closes what is under it. The button is the whole
 *  disclosure; an action beside it — a zip — is its sibling, never inside it. */
function Collapsible({
  icon,
  title,
  meta,
  action,
  open: initiallyOpen,
  nested,
  children,
}: {
  icon: string;
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  open: boolean;
  nested?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const body = useId();
  return (
    <section className={nested ? "media-card nested" : "media-card"}>
      <div className="media-card-head">
        <button
          type="button"
          className="media-toggle"
          aria-expanded={open}
          aria-controls={body}
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronDownIcon size={nested ? 12 : 14} className="chev" />
          <span aria-hidden>{icon}</span>
          <span className="media-card-title">{title}</span>
        </button>
        {meta && <span className="media-card-meta">{meta}</span>}
        {action && <span className="media-card-action">{action}</span>}
      </div>
      {open && (
        <div id={body} className="media-card-body">
          {children}
        </div>
      )}
    </section>
  );
}

function Rows({ entries, first, noun }: { entries: Entry[]; first: number; noun: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? entries : entries.slice(0, first);
  const more = entries.length - shown.length;
  return (
    <div className="media-rows">
      {shown.map((entry) => (
        <FileRow key={entry.file.path} entry={entry} />
      ))}
      {more > 0 && (
        <button type="button" className="small more-files" onClick={() => setAll(true)}>
          Show {more} more {noun}
          {more === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}

function FileRow({ entry, children }: { entry: Entry; children?: ReactNode }) {
  const { file } = entry;
  const take = entry.kind === "take";
  return (
    <div className={["media-row", `media-${entry.kind}`, take && !entry.inCut ? "out" : ""].filter(Boolean).join(" ")}>
      <span className={entry.fx ? "media-tile fx" : "media-tile"} aria-hidden>
        {take ? (entry.chunk ?? "?") : entry.icon}
      </span>
      <span className="media-row-text">
        {take ? (
          <span className="media-row-name">
            Chunk {entry.chunk} · take {entry.take}
          </span>
        ) : (
          <span className="media-row-name mono" title={file.name}>
            {file.name}
          </span>
        )}
        <span className={take ? "media-row-meta mono" : "media-row-meta"}>{entry.meta}</span>
      </span>
      {take && (
        <span className={entry.inCut ? "pill ok" : "pill quiet"}>
          {entry.inCut ? "✓ in the cut" : "not in the cut"}
        </span>
      )}
      {!take && <span className="media-row-size mono">{bytes(file.bytes)}</span>}
      {children ??
        (file.playable && entry.kind !== "piece" ? (
          <Player src={api.mediaUrl(file.path)} duration={file.duration_s} />
        ) : null)}
      <a
        className="icon-link"
        href={api.mediaUrl(file.path, true)}
        download
        aria-label={`Download ${file.name}`}
        title="Download"
      >
        <DownloadIcon size={16} />
      </a>
    </div>
  );
}

/** The export's documents, readable in place: `plan.md` is the guide to the
 *  edit, and reading it should not mean downloading it and finding something
 *  to open it with. */
function Documents({ scriptId, entries }: { scriptId: number; entries: Entry[] }) {
  const [reading, setReading] = useState<{ file: MediaFile; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(file: MediaFile) {
    setError(null);
    if (reading?.file.path === file.path) {
      setReading(null);
      return;
    }
    try {
      setReading({ file, text: await api.mediaText(file.path) });
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="media-section">
      <span className="media-section-label">📄 Documents</span>
      <div className="media-rows">
        {entries.map((entry) => (
          <FileRow key={entry.file.path} entry={entry}>
            {READABLE.test(entry.file.name) ? (
              <button
                type="button"
                className={reading?.file.path === entry.file.path ? "small on" : "small"}
                aria-expanded={reading?.file.path === entry.file.path}
                onClick={() => void toggle(entry.file)}
              >
                {reading?.file.path === entry.file.path ? "Close" : "Read"}
              </button>
            ) : null}
          </FileRow>
        ))}
      </div>
      {error && <p className="note error">{error}</p>}
      {reading && (
        <div className="media-reader">
          <div className="media-reader-head">
            <span className="mono">📄 {reading.file.name}</span>
            {reading.file.name === "plan.md" && (
              <span className="faint">
                preview · <Link to={scriptPath(scriptId, "/plan")}>open on the Plan page →</Link>
              </span>
            )}
          </div>
          <div className="media-reader-body">
            <DocumentView
              source={reading.text}
              filename={reading.file.name}
              downloadUrl={api.mediaUrl(reading.file.path, true)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

/** An export's zip. With exports in a folder of their own it is saved there,
 *  beside the episode folders — `004.zip` next to `004/` — so every zip ends up
 *  in one place rather than wherever the browser saves. Otherwise, a download.
 *  Either way it unzips to the episode's folder. */
export function ZipAction({
  scriptId,
  title,
  folder,
  large,
}: {
  scriptId: number;
  title: string;
  folder: string | null;
  /** The page header's version, which names the episode. */
  large?: boolean;
}) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SavedArchive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = large ? `${title} export as .zip` : "zip";

  if (!folder) {
    return (
      <a className={large ? "btn large" : "btn small"} href={api.archiveUrl(scriptId)} download>
        <DownloadIcon size={large ? 16 : 13} /> {label}
      </a>
    );
  }
  const where = folder.split("/").filter(Boolean).pop() ?? folder;

  async function save() {
    setSaving(true);
    setSaved(null);
    setError(null);
    try {
      setSaved(await api.saveArchive(scriptId));
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="zip-action">
      <button
        className={large ? "large" : "small"}
        onClick={() => void save()}
        disabled={saving}
        title={`Saves the zip in ${folder}, beside the episode folders, replacing one of the same name`}
      >
        <DownloadIcon size={large ? 16 : 13} /> {saving ? "Saving…" : large ? label : "Save zip"}
      </button>
      {saved && (
        <span className="faint" role="status">
          {saved.replaced ? "Replaced" : "Saved"} {saved.name} in {where}
        </span>
      )}
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

function total(entries: Entry[]): number {
  return entries.reduce((sum, e) => sum + e.file.bytes, 0);
}
