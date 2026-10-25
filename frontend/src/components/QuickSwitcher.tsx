import { useEffect, useId, useMemo, useRef, useState } from "react";
import { navigate, scriptPath } from "../router";
import type { Project, ScriptSummary } from "../types";

interface Choice {
  key: string;
  group: string;
  icon: string;
  label: string;
  meta?: string;
  run: () => void;
}

/** "⌘K" on a Mac, "Ctrl K" anywhere else — the keys this opens with. */
export const SWITCHER_KEYS =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";

/** Jump to any episode, any page of the one in hand, or another project, by
 *  typing a few letters of its name.
 *
 *  Names only: searching inside scripts, takes and files would need an index
 *  the app does not keep. Opened with ⌘K (Ctrl K) from anywhere, or from the
 *  search button in the top bar; Escape closes it and puts focus back. */
export function QuickSwitcher({
  open,
  onOpenChange,
  projects,
  scripts,
  episode,
  onProject,
}: {
  open: boolean;
  /** A state setter is ideal: stable, so the shortcut is listened for once. */
  onOpenChange: (open: boolean) => void;
  projects: Project[];
  scripts: ScriptSummary[];
  /** The episode in hand, whose pages are offered first. */
  episode: ScriptSummary | null;
  onProject: (id: number) => void;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;
  return (
    <Palette
      projects={projects}
      scripts={scripts}
      episode={episode}
      onProject={onProject}
      onClose={() => onOpenChange(false)}
    />
  );
}

function Palette({
  projects,
  scripts,
  episode,
  onProject,
  onClose,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
  episode: ScriptSummary | null;
  onProject: (id: number) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement | null>(null);
  const list = useRef<HTMLUListElement | null>(null);
  // Where focus was, so closing hands it back rather than dropping it on the page.
  const before = useRef<Element | null>(document.activeElement);
  const id = useId();

  useEffect(() => {
    input.current?.focus();
    const back = before.current;
    return () => {
      if (back instanceof HTMLElement) back.focus();
    };
  }, []);

  const choices = useMemo(() => {
    const projectName = new Map(projects.map((p) => [p.id, p.name]));
    const go = (to: string) => () => navigate(to);
    const all: Choice[] = [];
    if (episode) {
      for (const [tail, icon, label] of EPISODE_PAGES) {
        all.push({
          key: `page-${tail}`,
          group: `This episode · ${episode.title}`,
          icon,
          label,
          run: go(scriptPath(episode.id, tail)),
        });
      }
    }
    // Newest first: the episode being worked on is usually the latest.
    for (const script of [...scripts].reverse()) {
      all.push({
        key: `episode-${script.id}`,
        group: "Episodes",
        icon: "🎙️",
        label: script.title,
        meta: `${projectName.get(script.project_id) ?? ""} · ${script.chunks} chunk${script.chunks === 1 ? "" : "s"}`,
        run: go(scriptPath(script.id)),
      });
    }
    for (const [to, icon, label] of STUDIO_PAGES) {
      all.push({ key: `studio-${to}`, group: "Studio", icon, label, run: go(to) });
    }
    for (const project of projects) {
      all.push({
        key: `project-${project.id}`,
        group: "Projects",
        icon: "🗂️",
        label: project.name,
        meta: "switch project",
        run: () => {
          onProject(project.id);
          navigate("/");
        },
      });
    }
    return all;
  }, [projects, scripts, episode, onProject]);

  // Every word somewhere, and at least one in the name itself: "011 effects"
  // finds that episode's Effects page, but "01" does not list every page of
  // episode 011 just because its heading says so.
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = choices.filter((c) => {
    const label = c.label.toLowerCase();
    const text = `${label} ${c.meta ?? ""} ${c.group}`.toLowerCase();
    return words.every((w) => text.includes(w)) && (!words.length || words.some((w) => label.includes(w)));
  });
  const current = Math.min(active, Math.max(0, shown.length - 1));

  useEffect(() => {
    list.current?.querySelector(`[data-index="${current}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [current]);

  function choose(choice: Choice | undefined) {
    if (!choice) return;
    onClose();
    choice.run();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + shown.length) % Math.max(1, shown.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(shown[current]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  let lastGroup = "";
  return (
    <div className="qs-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="qs" role="dialog" aria-modal="true" aria-label="Jump to">
        <div className="qs-input">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={`${id}-list`}
            aria-activedescendant={shown.length ? `${id}-${current}` : undefined}
            aria-autocomplete="list"
            aria-label="Jump to an episode, page or project"
            placeholder="Jump to an episode, page or project…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          <kbd className="mono">esc</kbd>
        </div>
        <ul ref={list} id={`${id}-list`} role="listbox" aria-label="Places" className="qs-list">
          {shown.length === 0 && <li className="qs-empty">Nothing by that name.</li>}
          {shown.map((choice, index) => {
            const heading = choice.group !== lastGroup ? choice.group : null;
            lastGroup = choice.group;
            return [
              heading && (
                <li key={`h-${choice.key}`} role="presentation" className="qs-group">
                  {heading}
                </li>
              ),
              <li
                key={choice.key}
                id={`${id}-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === current}
                className={index === current ? "qs-option active" : "qs-option"}
                onMouseMove={() => index !== current && setActive(index)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(choice)}
              >
                <span className="qs-icon" aria-hidden>
                  {choice.icon}
                </span>
                <span className="qs-label">{choice.label}</span>
                {choice.meta && <span className="qs-meta">{choice.meta}</span>}
              </li>,
            ];
          })}
        </ul>
        <div className="qs-foot faint">
          <span>
            <kbd className="mono">↑</kbd> <kbd className="mono">↓</kbd> to move
          </span>
          <span>
            <kbd className="mono">↵</kbd> to open
          </span>
        </div>
      </div>
    </div>
  );
}

const EPISODE_PAGES: [string, string, string][] = [
  ["", "🎙️", "Workbench"],
  ["/effects", "✨", "Effects"],
  ["/media", "📦", "Media"],
  ["/plan", "🗺️", "Editing plan"],
  ["/publish", "🚀", "Publish pack"],
];

const STUDIO_PAGES: [string, string, string][] = [
  ["/", "🗂️", "Projects"],
  ["/cast", "🎭", "Cast"],
  ["/costs", "💰", "Costs"],
];
