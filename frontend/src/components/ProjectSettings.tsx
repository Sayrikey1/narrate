import { useRef, useState } from "react";
import { api } from "../api";
import { DeletePanel } from "./DeletePanel";
import { ProjectForm } from "./ProjectForm";
import type { Model, Project } from "../types";

/** The selected project's model, voice, delivery and cap — and deleting it. */
export function ProjectSettings({
  project,
  models,
  onChanged,
}: {
  project: Project;
  models: Model[];
  onChanged: () => void;
}) {
  // Where focus goes back to when the delete panel is cancelled, rather than
  // dropping to the page and making a keyboard user find their place again.
  const deleteTrigger = useRef<HTMLButtonElement | null>(null);
  const [deleting, setDeleting] = useState(false);

  return (
    <section className="panel" aria-labelledby="settings-h">
      <div className="card-head">
        <div className="card-head-text">
          <h2 id="settings-h">⚙️ {project.name} settings</h2>
          <p className="card-sub">
            Changing the voice, model or delivery means the next Generate makes every chunk again
            — it shows the price first.
          </p>
        </div>
        <button
          ref={deleteTrigger}
          className="small danger outline"
          onClick={() => setDeleting((v) => !v)}
          aria-expanded={deleting}
        >
          {deleting ? "Cancel" : "Delete project…"}
        </button>
      </div>

      {deleting && (
        <DeletePanel
          // Keyed by project, so a preview is never for the one selected before.
          key={`delete-${project.id}`}
          what="project"
          run={(body) => api.deleteProject(project.id, body)}
          onClose={() => {
            setDeleting(false);
            deleteTrigger.current?.focus();
          }}
          onDone={() => {
            setDeleting(false);
            onChanged();
          }}
        />
      )}

      <ProjectForm
        // Keyed by project: the form seeds its fields once, so without a
        // remount, selecting another project would save the first one's model
        // and voice onto it.
        key={project.id}
        models={models}
        project={project}
        onDone={onChanged}
      />
    </section>
  );
}
