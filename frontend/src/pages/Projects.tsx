import { useState } from "react";
import { usd } from "../api";
import { EpisodeTable } from "../components/EpisodeTable";
import { PlusIcon } from "../components/Icon";
import { PickUp } from "../components/PickUp";
import { ProjectCards } from "../components/ProjectCards";
import { ProjectForm } from "../components/ProjectForm";
import { ProjectSettings } from "../components/ProjectSettings";
import { initial } from "../components/Rail";
import { ScriptUpload } from "../components/ScriptUpload";
import { Link, navigate, scriptPath } from "../router";
import { PageHeader } from "../ui/layout";
import type { ChunkRow, Model, Project, ScriptSummary, Timeline, Voice } from "../types";

/** The landing page: every project, the episode to pick up, the selected
 *  project's episodes and settings, and adding an episode to it. */
export function ProjectsPage({
  projects,
  scripts,
  models,
  voices,
  selected,
  onSelect,
  onChanged,
  episode,
  timeline,
  chunks,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
  models: Model[];
  voices: Voice[];
  selected: number | null;
  onSelect: (id: number) => void;
  onChanged: () => void;
  /** The episode in hand: the one last opened in this project, else its newest. */
  episode: ScriptSummary | null;
  timeline: Timeline | null;
  chunks: ChunkRow[];
}) {
  // `#new-project` opens the form directly, for a bookmark or a screenshot.
  const [creating, setCreating] = useState(() => window.location.hash === "#new-project");
  const project = projects.find((p) => p.id === selected) ?? null;
  const episodes = scripts.filter((s) => s.project_id === selected);
  // The shell loads an episode's state when it changes, so for a moment it can
  // still hold the last one's: shown as loading rather than as the wrong facts.
  const loaded = episode !== null && timeline?.script_id === episode.id;

  return (
    <>
      <PageHeader
        kicker="👋 Welcome back to the studio"
        title="Projects"
        subtitle="A project holds a voice, a model and a spending cap. Every episode belongs to one."
        actions={
          <>
            <button
              className="large"
              onClick={() => setCreating((v) => !v)}
              aria-expanded={creating}
            >
              <PlusIcon size={16} /> New project
            </button>
            {project && (
              <a className="btn primary large" href="#add-episode">
                <span aria-hidden>📄</span> Add an episode
              </a>
            )}
          </>
        }
      />

      {creating && (
        <section className="panel" aria-labelledby="new-project-h">
          <div className="card-head">
            <div className="card-head-text">
              <h2 id="new-project-h">New project</h2>
              <p className="card-sub">
                Choose its model and voice now, while those choices are still free.
              </p>
            </div>
            <button className="ghost small" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
          <ProjectForm
            models={models}
            onDone={() => {
              setCreating(false);
              onChanged();
            }}
          />
        </section>
      )}

      <section className="section" aria-labelledby="projects-h">
        <div className="section-head">
          <h2 id="projects-h">
            Your projects <span className="count">{projects.length}</span>
          </h2>
          <Link className="link-quiet" to="/costs">
            Spend, and anything deleted →
          </Link>
        </div>
        {projects.length ? (
          <ProjectCards
            projects={projects}
            scripts={scripts}
            voices={voices}
            selected={selected}
            onSelect={onSelect}
          />
        ) : (
          <div className="panel">
            <div className="empty">
              <strong>No projects yet</strong>A project holds a voice, a model and a spending
              cap, and every episode belongs to one.
              {!creating && (
                <div className="toolbar empty-action">
                  <button className="primary" onClick={() => setCreating(true)}>
                    <PlusIcon /> New project
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <div className="row">
        <div className="grow">
          {episode && (
            <PickUp
              episode={episode}
              timeline={loaded ? timeline : null}
              chunks={loaded ? chunks : []}
            />
          )}
          {project && <EpisodeTable projectName={project.name} episodes={episodes} />}
          {project && <ProjectSettings project={project} models={models} onChanged={onChanged} />}
        </div>

        <div className="side">
          <section id="add-episode" className="panel" aria-labelledby="add-h">
            <div className="card-head">
              <h2 id="add-h">Add an episode</h2>
            </div>
            <ScriptUpload
              project={project}
              onCreated={(id) => {
                onChanged();
                navigate(scriptPath(id));
              }}
            />
          </section>

          <ScriptMarkers />

          {projects.length > 0 && <MonthSpend projects={projects} />}
        </div>
      </div>
    </>
  );
}

/** What can be typed into a script, kept beside the box it goes into. */
function ScriptMarkers() {
  const markers: [string, string, string][] = [
    ["✨", "[SFX: wind howling, 4s]", "places a sound effect"],
    ["🔁", "[SFX: soft rain, 30s, loop]", "loops under the passage"],
    ["🎯", "[@ 02:15]", "aims this line at 2:15 — reported as drift, never forced"],
    ["📑", "## Heading", "starts a YouTube chapter"],
    ["🎭", "Morag: her line", "spoken by Morag, if Morag is cast"],
    ["🙈", "<!-- a note -->", "a note to self, never narrated"],
  ];
  return (
    <section className="panel" aria-labelledby="markers-h">
      <div className="card-head">
        <div className="card-head-text">
          <h2 id="markers-h" className="card-title small">
            🧭 Script markers
          </h2>
          <p className="card-sub">
            Type these into the script. They're stripped before anything is sent — never spoken,
            never billed.
          </p>
        </div>
      </div>
      <ul className="markers">
        {markers.map(([icon, code, what]) => (
          <li key={code}>
            <span aria-hidden>{icon}</span>
            <span>
              <code>{code}</code>
              <span>{what}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** This month's spend by project — the figures each cap is measured against. */
function MonthSpend({ projects }: { projects: Project[] }) {
  const month = new Date().toLocaleString("en-GB", { month: "long" });
  const spending = projects.filter((p) => p.month_micros > 0);
  const total = spending.reduce((sum, p) => sum + p.month_micros, 0);
  const most = Math.max(1, ...spending.map((p) => p.month_micros));
  return (
    <section className="panel" aria-labelledby="spend-h">
      <div className="card-head">
        <h2 id="spend-h" className="card-title small">
          💰 {month} so far
        </h2>
        <span className="mono card-figure">{usd(total, 2)}</span>
      </div>
      <div className="spend-bars">
        {!spending.length && <p className="note flush">Nothing spent yet this month.</p>}
        {spending.map((p) => (
          <div key={p.id} className="meter-block">
            <div className="meter-head">
              <span>
                <span className="icon-tile sz-20" aria-hidden>
                  {initial(p.name)}
                </span>{" "}
                {p.name}
              </span>
              <span>{usd(p.month_micros, 2)}</span>
            </div>
            <div className="meter thick" aria-hidden>
              <div style={{ width: `${(p.month_micros / most) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <Link className="link-quiet" to="/costs">
        Full breakdown →
      </Link>
    </section>
  );
}
