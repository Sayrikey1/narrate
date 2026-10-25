import { Fragment } from "react";
import { Link } from "../router";
import type { Project, ScriptSummary } from "../types";
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
  onSearch,
}: {
  pattern: string | null;
  project: Project | null;
  episodes: ScriptSummary[];
  episode: ScriptSummary | null;
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
