import { Fragment } from "react";
import { usd } from "../api";
import { Link, scriptPath } from "../router";
import type { ActiveRun, Project, ScriptSummary } from "../types";
import { SWITCHER_KEYS } from "./QuickSwitcher";
import { EpisodeSwitch } from "./Rail";

/** The page's place: project, episode, page. The episode is a switch, so
 *  moving to the next episode's Effects page is one choice, not three. */

const EPISODE_PAGES: Record<string, string> = {
  "/script/:id": "Workbench",
  "/script/:id/effects": "Effects",
  "/script/:id/media": "Media",
  "/script/:id/plan": "Editing plan",
  "/script/:id/publish": "Publish pack",
};

const STUDIO_PAGES: Record<string, string> = {
  "/": "Projects",
  "/cast": "Cast",
  "/costs": "Costs",
};

export function Topbar({
  pattern,
  project,
  episodes,
  episode,
  runs,
  onSearch,
}: {
  pattern: string | null;
  project: Project | null;
  episodes: ScriptSummary[];
  episode: ScriptSummary | null;
  /** What is generating or being checked right now. */
  runs: ActiveRun[];
  /** Opens the quick switcher. */
  onSearch: () => void;
}) {
  const page = pattern ? (EPISODE_PAGES[pattern] ?? STUDIO_PAGES[pattern]) : undefined;
  const onEpisode = pattern !== null && pattern in EPISODE_PAGES;

  const trail = [
    // Costs spans every project, so it hangs off the studio, not one of them.
    pattern === "/costs" ? (
      <span key="studio" className="faint">
        Studio
      </span>
    ) : project ? (
      <Link key="project" to="/">
        {project.name}
      </Link>
    ) : null,
    onEpisode && episode ? (
      <EpisodeSwitch key="episode" episodes={episodes} episode={episode} crumb />
    ) : null,
    page ? (
      <span key="page" className="crumb-here" aria-current="page">
        {page}
      </span>
    ) : null,
  ].filter(Boolean);

  return (
    <header className="topbar">
      <nav aria-label="Breadcrumb" className="crumbs">
        {trail.map((part, index) => (
          <Fragment key={index}>
            {index > 0 && (
              <span className="crumb-sep" aria-hidden>
                /
              </span>
            )}
            {part}
          </Fragment>
        ))}
      </nav>
      {runs.length > 0 && <RunPill run={runs[0]!} more={runs.length - 1} />}
      <button type="button" className="qs-trigger" onClick={onSearch} aria-keyshortcuts="Meta+K Control+K">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
        <span className="qs-trigger-text">Jump to an episode or page…</span>
        <kbd className="mono">{SWITCHER_KEYS}</kbd>
      </button>
    </header>
  );
}

/** "Generating 012 · 5/8 · $0.84", on every page, until it is done. */
function RunPill({ run, more }: { run: ActiveRun; more: number }) {
  const verb = run.phase === "checking" ? "Checking" : "Generating";
  const progress =
    run.phase === "narration" && run.total
      ? `${run.done}/${run.total} · ${usd(run.spent_micros, 2)}`
      : run.phase === "effects"
        ? "effects"
        : "takes";
  return (
    <Link
      className="run-pill"
      to={scriptPath(run.script_id)}
      title={`${verb} ${run.title} — open its workbench`}
    >
      <span className="run-dot pulse" aria-hidden />
      <span>
        {verb} <strong>{run.title}</strong>
      </span>
      <span className="mono dim">{progress}</span>
      {more > 0 && <span className="faint">+{more}</span>}
    </Link>
  );
}
