import { usd } from "../api";
import { ProjectBar } from "../components/ProjectBar";
import { ScriptUpload } from "../components/ScriptUpload";
import { Link, navigate, scriptPath } from "../router";
import { Notice, Stat } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import type { Model, Project, ScriptSummary } from "../types";

/** The landing page: pick a project, add a script, open one. */
export function ProjectsPage({
  projects,
  scripts,
  models,
  selected,
  onSelect,
  onChanged,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
  models: Model[];
  selected: number | null;
  onSelect: (id: number) => void;
  onChanged: () => void;
}) {
  const visible = selected === null ? scripts : scripts.filter((s) => s.project_id === selected);
  const current = projects.find((p) => p.id === selected) ?? null;

  const columns: readonly Column<ScriptSummary>[] = [
    {
      header: "Title",
      cell: (script) => (
        <Link className="link-strong" to={scriptPath(script.id)}>
          {script.title}
        </Link>
      ),
    },
    { header: "Chunks", align: "num", cell: (s) => <span className="mono">{s.chunks}</span> },
    {
      header: "Characters",
      align: "num",
      cell: (s) => <span className="mono">{s.chars.toLocaleString()}</span>,
    },
    {
      header: "",
      align: "num",
      cell: (script) => (
        <button className="small" onClick={() => navigate(scriptPath(script.id))}>
          Open
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle={
          current
            ? `${current.name} — ${visible.length} script${visible.length === 1 ? "" : "s"}`
            : "a project holds a voice, a model and a spending cap"
        }
      />

      <ProjectBar
        projects={projects}
        models={models}
        selected={selected}
        onSelect={onSelect}
        onChanged={onChanged}
      />

      <Split
        main={
          <Card title="Scripts">
            <DataTable
              columns={columns}
              rows={visible}
              rowKey={(script) => script.id}
              caption="Scripts in this project"
              empty={{
                title: projects.length ? "No scripts in this project" : "Nothing here yet",
                body: projects.length
                  ? "Add one on the right — drop a .txt or .md file, or paste the text."
                  : "Create a project first, then add a script to it.",
              }}
            />
          </Card>
        }
        aside={
          <>
            <Card title="Add a script">
              <ScriptUpload
                projects={projects}
                preselect={selected}
                onCreated={(id) => {
                  onChanged();
                  if (id > 0) navigate(scriptPath(id));
                }}
              />
            </Card>

            {projects.length > 0 && (
              <Card title="Spend">
                {projects.map((project) => (
                  <Stat key={project.id} label={project.name} value={usd(project.spend_micros)} />
                ))}
                <Notice>
                  <Link to="/costs">Full breakdown →</Link>
                </Notice>
              </Card>
            )}
          </>
        }
      />
    </>
  );
}
