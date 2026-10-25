import { useEffect, useMemo, useState } from "react";
import { api, bytes } from "../api";
import { MediaLibrary, ZipAction } from "../components/MediaLibrary";
import { Variants } from "../components/Variants";
import { entriesOf, inScope, matches, sorted, type FileKind, type Sort } from "../mediaFiles";
import { Empty, Loadable } from "../ui/feedback";
import { PageHeader } from "../ui/layout";
import type { Project, ProjectMedia, ScriptSummary, Slot, Timeline } from "../types";

const KINDS: [FileKind | "all", string][] = [
  ["all", "All"],
  ["master", "🎧 Masters"],
  ["piece", "🧩 Pieces"],
  ["document", "📄 Documents"],
  ["effect", "✨ Effects"],
  ["take", "🎙️ Takes"],
];

const SORTS: [Sort, string][] = [
  ["timeline", "By timeline position"],
  ["newest", "Newest first"],
  ["largest", "Largest first"],
  ["name", "Name"],
];

/** Everything a project has produced, playable, readable and downloadable —
 *  this episode's by default, the whole project's a click away — and, beside
 *  it, the voices the episode can be exported in. */
export function MediaPage({
  project,
  scriptId,
  scripts,
  timeline,
  slots,
}: {
  project: Project | null;
  /** The episode in context. */
  scriptId: number | null;
  /** The project's episodes. */
  scripts: ScriptSummary[];
  timeline: Timeline | null;
  slots: Slot[];
}) {
  const [media, setMedia] = useState<ProjectMedia | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [wholeProject, setWholeProject] = useState(scriptId === null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("timeline");
  const [kind, setKind] = useState<FileKind | "all">("all");

  const projectId = project?.id ?? null;
  const load = () => {
    if (projectId === null) return;
    setLoading(true);
    setError(null);
    api
      .media(projectId)
      .then(setMedia)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [projectId]);

  const episode = scripts.find((s) => s.id === scriptId) ?? null;
  const all = useMemo(() => (media ? entriesOf(media, timeline) : []), [media, timeline]);
  const scoped = all.filter((e) => inScope(e, wholeProject ? null : scriptId, slots));
  const found = scoped.filter((e) => matches(e, query));
  const count = (k: FileKind | "all") => (k === "all" ? found.length : found.filter((e) => e.kind === k).length);
  const shown = sorted(
    found.filter((e) => kind === "all" || e.kind === kind),
    sort,
  );
  const elsewhere = all.filter((e) => e.scriptId !== null && e.scriptId !== scriptId);
  const exported = all.some((e) => e.scriptId === scriptId && e.kind === "master");

  if (!project) {
    return (
      <>
        <PageHeader title="Media" subtitle="Pick a project to see its files." />
        <Empty title="No project selected">
          Pick one on the projects page and its exports, effects and takes appear here.
        </Empty>
      </>
    );
  }

  return (
    <>
      <PageHeader
        kicker={`${project.name} · everything generated`}
        title="Media"
        subtitle={
          media ? (
            <>
              <span className="mono">{media.files}</span> file{media.files === 1 ? "" : "s"} ·{" "}
              <span className="mono">{bytes(media.bytes)}</span> on this computer · play, read or
              download any of it
            </>
          ) : (
            "Play, read or download anything the project has made."
          )
        }
        actions={
          episode && exported && media ? (
            <ZipAction scriptId={episode.id} title={episode.title} folder={media.export_folder} large />
          ) : undefined
        }
      />

      <section className="panel find-bar" aria-label="Find files">
        {episode && (
          <div className="seg-pill" role="group" aria-label="Show files for">
            <button type="button" aria-pressed={!wholeProject} onClick={() => setWholeProject(false)}>
              🎙️ This episode · {episode.title}
            </button>
            <button type="button" aria-pressed={wholeProject} onClick={() => setWholeProject(true)}>
              🗂️ Whole project
            </button>
          </div>
        )}
        <label className="search-box find-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            type="search"
            aria-label="Search files"
            placeholder="Search by filename, chunk or effect…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="find-sort">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            {SORTS.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="find-kinds" role="group" aria-label="Kind of file">
          {KINDS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className="chip"
              aria-pressed={kind === key}
              onClick={() => setKind(key)}
            >
              {label} <span className="mono chip-count">{count(key)}</span>
            </button>
          ))}
        </div>
      </section>

      <div className="row">
        <div className="grow">
          <Loadable loading={loading} error={error} rows={4} onRetry={load}>
            {shown.length ? (
              <MediaLibrary
                // A new scope starts with its own groups open, not the last one's.
                key={wholeProject ? "project" : "episode"}
                entries={shown}
                scripts={scripts}
                currentId={scriptId}
                wholeProject={wholeProject}
                exportFolder={media?.export_folder ?? null}
                searching={query.trim() !== ""}
              />
            ) : (
              <div className="panel">
                {query || kind !== "all" ? (
                  <Empty title="Nothing matches">
                    Nothing {wholeProject ? "in the project" : "in this episode"} matches that.
                  </Empty>
                ) : (
                  <Empty title="Nothing generated yet">
                    Generate the episode, then export it — the masters, their timeline-named
                    pieces, the effects and every take appear here.
                  </Empty>
                )}
              </div>
            )}
            {!wholeProject && elsewhere.length > 0 && (
              <div className="dashed-note row-note">
                <span aria-hidden>🗂️</span>
                <span>
                  The other episodes in {project.name} have{" "}
                  <span className="mono">{elsewhere.length}</span> more file
                  {elsewhere.length === 1 ? "" : "s"}.
                </span>
                <button type="button" className="small" onClick={() => setWholeProject(true)}>
                  Show the whole project
                </button>
              </div>
            )}
          </Loadable>
        </div>

        <div className="side">
          {scriptId !== null && <Variants scriptId={scriptId} title={episode?.title} />}
          {media && media.bytes > 0 && <OnDisk media={media} />}
        </div>
      </div>
    </>
  );
}

/** Where the project's disk space goes: takes, exports, effects. */
function OnDisk({ media }: { media: ProjectMedia }) {
  const of = (kind: "take" | "export" | "effect") =>
    media.groups.filter((g) => g.kind === kind).reduce((sum, g) => sum + g.bytes, 0);
  const parts: [string, string, number][] = [
    ["🎙️ Takes", "var(--narr)", of("take")],
    ["📦 Exports", "var(--ac-bar)", of("export")],
    ["✨ Effects", "var(--fx)", of("effect")],
  ];
  return (
    <section className="panel" aria-labelledby="disk-h">
      <div className="card-head">
        <h2 id="disk-h" className="card-title">
          💽 On disk
        </h2>
        <span className="mono card-figure">{bytes(media.bytes)}</span>
      </div>
      <div className="stack-bar" aria-hidden>
        {parts.map(([label, colour, size]) =>
          size > 0 ? (
            <span key={label} style={{ width: `${(size / media.bytes) * 100}%`, background: colour }} />
          ) : null,
        )}
      </div>
      <ul className="legend">
        {parts.map(([label, colour, size]) => (
          <li key={label}>
            <span className="legend-swatch" style={{ background: colour }} aria-hidden />
            {label}
            <span className="mono">{bytes(size)}</span>
          </li>
        ))}
      </ul>
      <p className="note flush">
        Every take is kept, even the ones not in the cut — what they cost stays on the record.
      </p>
    </section>
  );
}
