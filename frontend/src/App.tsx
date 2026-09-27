import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { AutoThemeIcon, MoonIcon, SparkIcon, SunIcon, WaveIcon } from "./components/Icon";
import { Transport } from "./components/Transport";
import { useCatalogue, useScript } from "./hooks";
import { Notice } from "./ui/feedback";
import { CastPage } from "./pages/Cast";
import { CostsPage } from "./pages/Costs";
import { EffectsPage } from "./pages/Effects";
import { MediaPage } from "./pages/Media";
import { PlanPage } from "./pages/Plan";
import { PublishPage } from "./pages/Publish";
import { ProjectsPage } from "./pages/Projects";
import { ScriptPage } from "./pages/Script";
import { Link, RouterProvider, scriptPath, useRoute } from "./router";
import * as transport from "./transport";

export function App() {
  return (
    <RouterProvider>
      <Shell />
    </RouterProvider>
  );
}

function Shell() {
  const route = useRoute();
  const { projects, scripts, models, refresh } = useCatalogue();
  const [projectId, setProjectId] = useState<number | null>(null);

  const scriptId = route.params.id ? Number(route.params.id) : null;
  const script = scripts.find((s) => s.id === scriptId) ?? null;

  // Opening a script implies its project, so the sidebar and the media page
  // follow along without the selection having to be made twice.
  // A selection that no longer exists — a project just deleted — falls back to
  // the first one, or "Add a script" would post to the deleted project while
  // the picker showed another.
  useEffect(() => {
    if (script) setProjectId(script.project_id);
    else if (!projects.some((p) => p.id === projectId)) setProjectId(projects[0]?.id ?? null);
  }, [script, projects, projectId]);

  const project = projects.find((p) => p.id === projectId) ?? null;
  // The script's own model where it has one — not always the project's.
  const model = models.find((m) => m.model_id === (script?.model_id ?? project?.model_id));

  const state = useScript(scriptId);
  const { timeline, chunks, slots, cost, plan } = state;

  // Feed the transport whatever the current script's timeline holds, so
  // pressing play plays the episode rather than one clip.
  // Only an episode that still exists: a deleted one's clips may be gone from
  // disk, and it should not keep playing from the transport bar either way.
  useEffect(() => {
    if (!timeline || !scripts.some((s) => s.id === timeline.script_id)) {
      transport.load([]);
      return;
    }
    transport.load(
      timeline.entries
        .filter((e) => e.generated && (e.take_id || e.effect_id))
        .map((e) => ({
          id: `${e.kind}-${e.index}`,
          src: e.take_id ? api.audioTake(e.take_id) : api.audioEffect(e.effect_id!),
          startAt: e.start_s,
          duration: e.duration_s,
          lane: e.kind === "narration" ? ("narration" as const) : ("effect" as const),
        })),
    );
  }, [timeline, scripts]);

  const nav = useMemo(
    () => [
      { to: "/", label: "Projects", icon: null },
      { to: "/cast", label: "Cast", icon: null },
      ...(scriptId !== null
        ? [
            { to: scriptPath(scriptId), label: "Script", icon: <WaveIcon /> },
            {
              to: scriptPath(scriptId, "/effects"),
              label: `Effects${slots.length ? ` (${slots.length})` : ""}`,
              icon: <SparkIcon />,
            },
            { to: scriptPath(scriptId, "/media"), label: "Media", icon: null },
            { to: scriptPath(scriptId, "/plan"), label: "Plan", icon: null },
            { to: scriptPath(scriptId, "/publish"), label: "Publish", icon: null },
          ]
        : []),
      { to: "/costs", label: "Costs", icon: null },
    ],
    [scriptId, slots.length],
  );

  return (
    <div className="layout">
      <aside className="rail">
        <div className="brand">
          <span className="brand-name">narrate</span>
          <ThemeToggle />
        </div>

        <nav>
          {nav.map((item) => (
            <Link key={item.to} to={item.to} className="rail-link">
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>

        {project && (
          <div className="rail-foot">
            <div className="faint">Project</div>
            <div>{project.name}</div>
            <div className="faint mono">{project.model_id.replace("eleven_", "")}</div>
          </div>
        )}
      </aside>

      <main className="main">
        <Transport title={script?.title} />

        <div className="page">
          {state.error && (
            <Notice tone="error" title="Could not load this script">
              {state.error}
              <button className="ghost small" onClick={() => void state.refresh()}>
                Try again
              </button>
            </Notice>
          )}

          {route.pattern === "/" && (
            <ProjectsPage
              projects={projects}
              scripts={scripts}
              models={models}
              selected={projectId}
              onSelect={setProjectId}
              onChanged={refresh}
            />
          )}

          {route.pattern === "/script/:id" && scriptId !== null && (
            <ScriptPage
              scriptId={scriptId}
              script={script}
              project={project}
              model={model}
              timeline={timeline}
              chunks={chunks}
              cost={cost}
              onRefresh={() => {
                void state.refresh();
                void refresh();
              }}
            />
          )}

          {route.pattern === "/script/:id/effects" && scriptId !== null && (
            <EffectsPage
              scriptId={scriptId}
              script={script}
              slots={slots}
              onRefresh={() => void state.refresh()}
            />
          )}

          {route.pattern === "/script/:id/media" && (
            <MediaPage project={project} scriptId={scriptId} />
          )}

          {route.pattern === "/script/:id/plan" && scriptId !== null && (
            <PlanPage scriptId={scriptId} script={script} plan={plan} />
          )}
          {route.pattern === "/script/:id/publish" && scriptId !== null && (
            <PublishPage scriptId={scriptId} script={script} />
          )}

          {route.pattern === "/cast" && (
            <CastPage
              projectId={projectId}
              projectName={project?.name ?? ""}
              scripts={scripts.filter((s) => s.project_id === projectId)}
              onChanged={refresh}
            />
          )}

          {route.pattern === "/costs" && (
            <CostsPage projects={projects} scripts={scripts} onChanged={() => void refresh()} />
          )}

          {route.pattern === null && (
            <div className="panel">
              <div className="empty">
                <strong>No such page</strong>
                <Link to="/">Back to projects →</Link>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

/** An explicit light/dark choice, remembered. Absent one, the OS preference
 *  applies — which is why the stored value is a tri-state rather than a bool. */
function ThemeToggle() {
  const [theme, setTheme] = useState<"system" | "light" | "dark">(() => {
    // `?theme=light` wins, so a link or a screenshot can pin the appearance
    // without touching the viewer's stored preference first.
    const asked = new URLSearchParams(window.location.search).get("theme");
    if (asked === "light" || asked === "dark") return asked;
    return (localStorage.getItem("narrate-theme") as "light" | "dark" | null) ?? "system";
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
      localStorage.removeItem("narrate-theme");
    } else {
      root.setAttribute("data-theme", theme);
      localStorage.setItem("narrate-theme", theme);
    }
  }, [theme]);

  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Glyph = theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : AutoThemeIcon;

  return (
    <button
      className="ghost small theme-toggle"
      onClick={() => setTheme(next)}
      title={`Theme: ${theme === "system" ? "following the system" : theme}. Click for ${next}.`}
      aria-label={`Theme: ${theme}. Switch to ${next}.`}
    >
      <Glyph />
    </button>
  );
}
