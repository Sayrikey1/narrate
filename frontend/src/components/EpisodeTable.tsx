import { useState } from "react";
import { usd } from "../api";
import { Link, scriptPath } from "../router";
import type { EpisodeStatus, ScriptSummary } from "../types";

type Filter = "all" | "needs" | "progress" | "done";

const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["needs", "Needs you"],
  ["progress", "In progress"],
  ["done", "Done"],
];

/** The project's episodes, each with where it is in the pipeline — script,
 *  audio, check, export, publish — what it has cost, and when it last moved. */
export function EpisodeTable({
  projectName,
  episodes,
  statuses,
  failed,
}: {
  projectName: string;
  episodes: ScriptSummary[];
  /** Each episode's status, by its id; fetched once for the page. */
  statuses: Record<number, EpisodeStatus>;
  /** The statuses could not be fetched. */
  failed?: boolean;
}) {
  const [filter, setFilter] = useState<Filter>("all");

  const group = (s: EpisodeStatus | undefined): Filter =>
    !s ? "progress" : s.needs_you ? "needs" : s.done ? "done" : "progress";
  const count = (f: Filter) => (f === "all" ? episodes.length : episodes.filter((e) => group(statuses[e.id]) === f).length);
  const shown = episodes.filter((e) => filter === "all" || group(statuses[e.id]) === filter);

  return (
    <section className="panel" aria-labelledby="eps-h">
      <div className="card-head">
        <h2 id="eps-h">
          Episodes in {projectName} <span className="count">{episodes.length}</span>
        </h2>
        {episodes.length > 0 && (
          <div className="seg-pill" role="group" aria-label="Filter episodes">
            {FILTERS.map(([key, label]) => (
              <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                {label} <span className="mono chip-count">{count(key)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {episodes.length === 0 ? (
        <div className="empty">
          <strong>No episodes yet</strong>
          Add one with the card on the right — drop a .txt or .md file, or paste the text.
        </div>
      ) : shown.length === 0 ? (
        <div className="empty">
          <strong>None {filter === "needs" ? "need you" : filter === "done" ? "done yet" : "in progress"}</strong>
          {filter === "needs" ? "Nothing is flagged, failed or out of date." : "Pick another filter to see them."}
        </div>
      ) : (
        <div className="table-scroll">
          <table className="episodes">
            <caption className="sr-only">
              Episodes in {projectName}, with where each one is in the pipeline
            </caption>
            <thead>
              <tr>
                <th scope="col">Episode</th>
                <th scope="col">Script · Audio · Check · Export · Publish</th>
                <th scope="col" className="num">
                  Runtime
                </th>
                <th scope="col" className="num">
                  Spent
                </th>
                <th scope="col">Updated</th>
                <th scope="col">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((episode) => {
                const status = statuses[episode.id];
                const line = status ? describe(status) : null;
                return (
                  <tr key={episode.id}>
                    <td>
                      <Link className="episode-cell" to={scriptPath(episode.id)} title={episode.title}>
                        <span className="icon-tile sz-42" aria-hidden>
                          🎙️
                        </span>
                        <span className="episode-cell-text">
                          <span className="episode-cell-title">{episode.title}</span>
                          <span className="episode-cell-meta">
                            {episode.chunks} chunk{episode.chunks === 1 ? "" : "s"} ·{" "}
                            {episode.chars.toLocaleString()} chars
                          </span>
                        </span>
                      </Link>
                    </td>
                    <td>
                      {status ? (
                        <span className="pipeline">
                          <span className="pipeline-bars" aria-hidden>
                            {stages(status).map((stage, i) => (
                              <span key={i} className={`pipeline-bar ${stage}`} />
                            ))}
                          </span>
                          <span className={`pipeline-text ${line!.tone}`}>{line!.text}</span>
                        </span>
                      ) : (
                        <span className="faint">{failed ? "—" : "…"}</span>
                      )}
                    </td>
                    <td className="num mono dim">
                      {status && status.with_audio > 0 ? clock(status.runtime_s) : "—"}
                    </td>
                    <td className="num mono dim">{status ? usd(status.spent_micros) : "…"}</td>
                    <td className="dim nowrap">{status ? ago(status.last_activity) : ""}</td>
                    <td className="num">
                      <Link className="btn small" to={scriptPath(episode.id)}>
                        Open<span className="sr-only"> {episode.title}</span> →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type Stage = "done" | "warn" | "bad" | "part" | "off";

/** One bar each for script, audio, check, export and publish. */
export function stages(s: EpisodeStatus): Stage[] {
  const audio: Stage =
    s.with_audio === s.chunks && s.chunks > 0 ? "done" : s.failed.length ? "bad" : s.with_audio > 0 ? "part" : "off";
  const check: Stage =
    s.flagged || s.review
      ? "warn"
      : s.with_audio > 0 && s.unchecked === 0
        ? "done"
        : s.unchecked < s.with_audio
          ? "part"
          : "off";
  const exported: Stage = !s.exported_at ? "off" : s.export_current ? "done" : "warn";
  const publish: Stage = s.title_chosen && s.chapters_usable ? "done" : "off";
  return ["done", audio, check, exported, publish];
}

/** The one thing to know about an episode, most pressing first. */
export function describe(s: EpisodeStatus): { text: string; tone: string } {
  if (s.failed.length) {
    return {
      text:
        s.failed.length === 1
          ? `⛔ Chunk ${s.failed[0]} didn't generate`
          : `⛔ ${s.failed.length} chunks didn't generate`,
      tone: "bad",
    };
  }
  if (s.flagged || s.review) {
    const parts = [s.flagged && `${s.flagged} flagged`, s.review && `${s.review} worth a listen`].filter(Boolean);
    return { text: `⚠️ ${parts.join(" · ")}`, tone: "warn" };
  }
  if (s.with_audio < s.chunks) {
    const about = s.outstanding_micros ? ` — about ${usd(s.outstanding_micros, 2)}` : "";
    return s.with_audio === 0
      ? { text: `📝 Script only${about} to generate`, tone: "dim" }
      : { text: `🎙️ ${s.chunks - s.with_audio} of ${s.chunks} chunks to generate${about}`, tone: "warn" };
  }
  const unchecked = s.unchecked ? ` · ${s.unchecked} not checked` : "";
  if (s.exported_at && !s.export_current) {
    return { text: "♻️ Export out of date — the cut has changed since", tone: "warn" };
  }
  if (!s.exported_at) return { text: `✅ Ready to export${unchecked}`, tone: "ok" };
  return {
    text: `${s.title_chosen && s.chapters_usable ? "🚀 Exported — publish pack ready" : `📦 Exported ${ago(s.exported_at)}`}${unchecked}`,
    tone: "ok",
  };
}

/** "today", "yesterday", or "23 Oct". */
export function ago(iso: string): string {
  const then = new Date(iso);
  const days = Math.floor((startOfDay(new Date()) - startOfDay(then)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return then.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
