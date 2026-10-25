import { usd } from "../api";
import type { Project, ScriptSummary, Voice } from "../types";
import { initial } from "./Rail";

/** Every project as a card: what it is made with, what it has spent, and how
 *  close it is to its cap. Choosing one scopes the menu and the rest of this
 *  page to it. */
export function ProjectCards({
  projects,
  scripts,
  voices,
  selected,
  onSelect,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
  voices: Voice[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="project-cards">
      {projects.map((project) => (
        <ProjectCard
          key={project.id}
          project={project}
          episodes={scripts.filter((s) => s.project_id === project.id).length}
          voice={voices.find((v) => v.voice_id === project.voice_id) ?? null}
          selected={project.id === selected}
          onSelect={() => onSelect(project.id)}
        />
      ))}
    </div>
  );
}

function ProjectCard({
  project,
  episodes,
  voice,
  selected,
  onSelect,
}: {
  project: Project;
  episodes: number;
  voice: Voice | null;
  selected: boolean;
  onSelect: () => void;
}) {
  const pct = project.cap_used_pct;
  const capped = project.cap_micros !== null;
  const tone = !capped ? "" : pct >= 100 ? "danger" : pct >= 80 ? "warn" : "";

  return (
    <button
      type="button"
      className={selected ? "project-card selected" : "project-card"}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="project-card-top">
        <span className="icon-tile sz-48" aria-hidden>
          {initial(project.name)}
        </span>
        {selected ? (
          <span className="pill accent">✓ Selected</span>
        ) : capped && pct >= 100 ? (
          <span className="pill bad">⛔ Cap reached</span>
        ) : capped && pct >= 80 ? (
          <span className="pill warn">⚠️ Near cap</span>
        ) : voice?.mock ? (
          <span className="pill quiet">Practice · free</span>
        ) : null}
      </span>

      <span className="project-card-name">
        <span className="project-card-title">{project.name}</span>
        <span className="project-card-meta">
          {episodes} episode{episodes === 1 ? "" : "s"} · 🗣️{" "}
          {voice?.mock ? (
            <span className="warn">stand-in voice</span>
          ) : voice ? (
            voice.name.split(" - ")[0]
          ) : project.voice_id ? (
            <span className="mono">{project.voice_id.slice(0, 10)}</span>
          ) : (
            <span className="warn">no voice yet</span>
          )}{" "}
          · <span className="mono">{project.model_id.replace("eleven_", "")}</span>
        </span>
      </span>

      <span className="tiles">
        <span className="tile">
          <span className="tile-label">Spent</span>
          <span className="tile-value">{usd(project.spend_micros)}</span>
        </span>
        <span className="tile">
          <span className="tile-label">Waste</span>
          <span className={project.waste_pct >= 25 ? "tile-value warn" : "tile-value"}>
            {project.spend_micros > 0 ? `${project.waste_pct}%` : "—"}
          </span>
        </span>
      </span>

      {capped ? (
        <span className="meter-block">
          <span className="meter-head">
            <span>Cap this month</span>
            <span className={tone ? (tone === "danger" ? "bad" : "warn") : undefined}>
              {usd(project.month_micros, 2)} / {dollars(project.cap_micros ?? 0)} · {pct}%
            </span>
          </span>
          <span className={`meter ${tone}`} aria-hidden>
            <span style={{ width: `${Math.min(100, pct)}%` }} />
          </span>
        </span>
      ) : (
        <span className="meter-block">
          <span className="meter-head">
            <span>No cap</span>
            <span className="plain">
              {voice?.mock ? "mock provider, nothing billed" : "spending isn't limited"}
            </span>
          </span>
          <span className="meter dashed" aria-hidden />
        </span>
      )}
    </button>
  );
}

/** "$15" for a round cap, "$12.50" for one that is not. */
function dollars(micros: number): string {
  return usd(micros, 2).replace(/\.00$/, "");
}
