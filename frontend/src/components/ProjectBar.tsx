import { useState } from "react";
import { usd } from "../api";
import { Card } from "../ui/layout";
import { Badge } from "../ui/feedback";
import { DataTable, type Column } from "../ui/Table";
import { PlusIcon } from "./Icon";
import { ProjectForm } from "./ProjectForm";
import type { Model, Project } from "../types";

/** The project list, and the two disclosures that hang off it.
 *
 *  Was 303 lines doing four jobs — this list, the create form, the profile
 *  editor and the model select. The forms live in `ProjectForm` and
 *  `ModelField` now; what is left is the table and which panel is open.
 *
 *  Selecting a project scopes the rest of the screen to it. Voice and delivery
 *  are set here, before anything is generated, which is the point at which
 *  those choices are still free.
 */
export function ProjectBar({
  projects,
  models,
  selected,
  onSelect,
  onChanged,
}: {
  projects: Project[];
  models: Model[];
  selected: number | null;
  onSelect: (id: number) => void;
  onChanged: () => void;
}) {
  // `#new-project` opens the form directly, which is also what makes the voice
  // picker reachable without a click for a screenshot or a bookmark.
  const [open, setOpen] = useState<"none" | "new" | "profile">(() =>
    window.location.hash === "#new-project" ? "new" : "none",
  );

  const current = projects.find((p) => p.id === selected) ?? null;

  const columns: readonly Column<Project>[] = [
    {
      header: "Name",
      cell: (project) => (
        <button
          className={project.id === selected ? "primary small" : "small"}
          onClick={() => onSelect(project.id)}
          aria-pressed={project.id === selected}
        >
          {project.name}
        </button>
      ),
    },
    {
      header: "Model",
      cell: (project) => (
        <span className="dim mono">{project.model_id.replace("eleven_", "")}</span>
      ),
    },
    {
      header: "Voice",
      cell: (project) =>
        project.voice_id ? (
          <span className="dim mono">{project.voice_id.slice(0, 12)}</span>
        ) : (
          <Badge tone="warn">unset</Badge>
        ),
    },
    {
      header: "Spend",
      align: "num",
      cell: (project) => <span className="mono">{usd(project.spend_micros)}</span>,
    },
    {
      header: "Waste",
      align: "num",
      cell: (project) => <span className="mono">{project.waste_pct}%</span>,
    },
    {
      header: "Cap",
      cell: (project) =>
        project.cap_micros !== null ? (
          <Badge tone={project.cap_used_pct >= 80 ? "planned" : undefined}>
            {project.cap_used_pct}%
          </Badge>
        ) : (
          <span className="faint">—</span>
        ),
    },
  ];

  return (
    <Card title="Projects">
      <DataTable
        compact
        columns={columns}
        rows={projects}
        rowKey={(project) => project.id}
        caption="Projects, with what each has spent"
        empty={{
          title: "No projects yet",
          body: "A project holds a voice, a model and a spending cap, and every script belongs to one.",
          action: (
            <button className="primary" onClick={() => setOpen("new")}>
              <PlusIcon /> New project
            </button>
          ),
        }}
      />

      {projects.length > 0 && (
        <div className="toolbar project-actions">
          <button
            className="primary"
            onClick={() => setOpen((v) => (v === "new" ? "none" : "new"))}
            aria-expanded={open === "new"}
          >
            {open === "new" ? (
              "Cancel"
            ) : (
              <>
                <PlusIcon /> New project
              </>
            )}
          </button>
          {current && (
            <button
              onClick={() => setOpen((v) => (v === "profile" ? "none" : "profile"))}
              aria-expanded={open === "profile"}
            >
              {open === "profile" ? "Done" : `Voice & delivery — ${current.name}`}
            </button>
          )}
        </div>
      )}

      {open === "new" && (
        <ProjectForm
          models={models}
          onDone={() => {
            setOpen("none");
            onChanged();
          }}
        />
      )}

      {open === "profile" && current && (
        <ProjectForm
          models={models}
          project={current}
          onDone={() => {
            setOpen("none");
            onChanged();
          }}
        />
      )}
    </Card>
  );
}
