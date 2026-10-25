import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { usd } from "../api";
import { Link, navigate, scriptPath } from "../router";
import { Menu } from "../ui/Menu";
import type { Project, ScriptSummary } from "../types";
import {
  BrandMark,
  ChevronDownIcon,
  ChevronsIcon,
  DarkThemeIcon,
  LightThemeIcon,
  SystemThemeIcon,
} from "./Icon";

/** The menu down the left: the project in hand, the studio's pages, and the
 *  pages of one episode.
 *
 *  The episode section stays when you leave the episode — on Projects, Cast
 *  or Costs it still names the one you were working on, so getting back is one
 *  click rather than a hunt through the episode list. */
export function Rail({
  projects,
  project,
  projectMeta,
  onProject,
  episodes,
  episode,
  totalSpend,
  flagged,
  effects,
}: {
  projects: Project[];
  project: Project | null;
  /** "Eleven v3 · Rachel": the project's model and voice, as names. */
  projectMeta: string;
  onProject: (id: number) => void;
  /** The selected project's episodes. */
  episodes: ScriptSummary[];
  episode: ScriptSummary | null;
  totalSpend: number;
  /** Takes in the cut still flagged, for the Workbench badge. */
  flagged: number;
  /** Effect placements, for the count beside Effects. */
  effects: number;
}) {
  return (
    <aside className="rail" aria-label="Main navigation">
      <div className="brand">
        <BrandMark />
        <span className="brand-name">narrate</span>
        <ThemeToggle />
      </div>

      {project && (
        <Menu
          className="rail-project"
          label={`Project ${project.name} — switch project`}
          heading="Projects"
          trigger={
            <>
              <span className="icon-tile sz-40" aria-hidden>
                {initial(project.name)}
              </span>
              <span className="rail-project-text">
                <span className="rail-project-name">{project.name}</span>
                <span className="rail-project-meta">{projectMeta}</span>
              </span>
              <ChevronsIcon className="chevron" />
            </>
          }
          items={projects.map((p) => ({
            key: p.id,
            label: p.name,
            meta: usd(p.spend_micros, 2),
            icon: (
              <span className="icon-tile" aria-hidden>
                {initial(p.name)}
              </span>
            ),
            current: p.id === project.id,
            onSelect: () => onProject(p.id),
          }))}
          footer={{ key: "all", label: "All projects →", onSelect: () => navigate("/") }}
        />
      )}

      <nav aria-label="Studio">
        <div className="nav-label">Studio</div>
        <NavItem to="/" icon="🗂️" label="Projects" />
        <NavItem to="/cast" icon="🎭" label="Cast" />
        <NavItem
          to="/costs"
          icon="💰"
          label="Costs"
          extra={<span className="nav-count">{usd(totalSpend, 2)}</span>}
        />
      </nav>

      {episode && (
        <nav aria-label="Episode">
          <div className="nav-label">
            <span>Episode</span>
            <EpisodeSwitch episodes={episodes} episode={episode} />
          </div>
          <NavItem
            to={scriptPath(episode.id)}
            icon="🎙️"
            label="Workbench"
            extra={
              flagged > 0 ? (
                <span className="nav-badge" title={`${flagged} flagged take${flagged === 1 ? "" : "s"}`}>
                  {flagged}
                </span>
              ) : null
            }
          />
          <NavItem
            to={scriptPath(episode.id, "/effects")}
            icon="✨"
            label="Effects"
            extra={effects > 0 ? <span className="nav-count">{effects}</span> : null}
          />
          <NavItem to={scriptPath(episode.id, "/media")} icon="📦" label="Media" />
          <NavItem to={scriptPath(episode.id, "/plan")} icon="🗺️" label="Plan" />
          <NavItem to={scriptPath(episode.id, "/publish")} icon="🚀" label="Publish" />
        </nav>
      )}

      {project?.cap_micros != null && <CapMeter project={project} />}
    </aside>
  );
}

function NavItem({
  to,
  icon,
  label,
  extra,
}: {
  to: string;
  icon: string;
  label: string;
  extra?: ReactNode;
}) {
  return (
    <Link to={to} className="nav-item">
      <span className="nav-icon" aria-hidden>
        {icon}
      </span>
      {label}
      {extra}
    </Link>
  );
}

/** Another episode of the same project, on the same page you are on. */
export function EpisodeSwitch({
  episodes,
  episode,
  crumb,
}: {
  episodes: ScriptSummary[];
  episode: ScriptSummary;
  /** The larger version that sits in the breadcrumb. */
  crumb?: boolean;
}) {
  return (
    <Menu
      className={crumb ? "switch crumb" : "switch"}
      label={`Episode ${episode.title} — switch episode`}
      heading="Episodes"
      trigger={
        <>
          <span className="switch-label" title={episode.title}>
            {episode.title}
          </span>
          <ChevronDownIcon />
        </>
      }
      items={[...episodes].reverse().map((e) => ({
        key: e.id,
        label: e.title,
        meta: `${e.chunks} chunk${e.chunks === 1 ? "" : "s"} · ${e.chars.toLocaleString()} chars`,
        current: e.id === episode.id,
        onSelect: () => navigate(samePage(e.id)),
      }))}
    />
  );
}

/** The page you are on, for another episode: Effects stays Effects. */
function samePage(id: number): string {
  const tail = window.location.pathname.match(/^\/script\/\d+(\/[a-z]+)?/)?.[1] ?? "";
  return scriptPath(id, tail);
}

function CapMeter({ project }: { project: Project }) {
  const pct = project.cap_used_pct;
  const tone = pct >= 100 ? " danger" : pct >= 80 ? " warn" : "";
  return (
    <div className="rail-meters">
      <div className="meter-head">
        <span>Monthly cap</span>
        <span>{pct}%</span>
      </div>
      <div className={`meter${tone}`} aria-hidden>
        <div style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <div className="foot">
        {usd(project.month_micros, 2)} of {usd(project.cap_micros ?? 0, 2)} this month
      </div>
    </div>
  );
}

/** The first letter of a name, for its tile, until projects carry an icon. */
export function initial(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

/** An explicit light/dark choice, remembered. Absent one, the OS preference
 *  applies — which is why the stored value is a tri-state rather than a bool. */
function ThemeToggle() {
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    // `?theme=light` wins, so a link or a screenshot can pin the appearance
    // without touching the viewer's stored preference first.
    const asked = new URLSearchParams(window.location.search).get("theme");
    if (asked === "light" || asked === "dark") return asked;
    try {
      return (localStorage.getItem("narrate-theme") as "light" | "dark" | null) ?? "system";
    } catch {
      return "system";
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    try {
      if (theme === "system") {
        root.removeAttribute("data-theme");
        localStorage.removeItem("narrate-theme");
      } else {
        root.setAttribute("data-theme", theme);
        localStorage.setItem("narrate-theme", theme);
      }
    } catch {
      // Storage refused: the choice still applies for this visit.
      if (theme !== "system") root.setAttribute("data-theme", theme);
    }
  }, [theme]);

  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Glyph = theme === "light" ? LightThemeIcon : theme === "dark" ? DarkThemeIcon : SystemThemeIcon;
  const named = theme === "system" ? "following the system" : theme;

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next)}
      title={`Theme: ${named}. Click for ${next}.`}
      aria-label={`Theme: ${named}. Switch to ${next}.`}
    >
      <Glyph />
    </button>
  );
}
