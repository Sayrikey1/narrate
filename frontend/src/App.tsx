import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { QuickSwitcher } from "./components/QuickSwitcher";
import { Rail } from "./components/Rail";
import { Topbar } from "./components/Topbar";
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
import { Link, RouterProvider, useRoute } from "./router";
import * as transport from "./transport";

export function App() {
  return (
    <RouterProvider>
      <Shell />
    </RouterProvider>
  );
}

/** The episode last opened, so leaving it for Projects or Costs keeps it in
 *  the menu and in the player. */
const EPISODE_KEY = "narrate-episode";

function rememberedEpisode(): number | null {
  try {
    const stored = localStorage.getItem(EPISODE_KEY);
    return stored ? Number(stored) : null;
  } catch {
    return null;
  }
}

function Shell() {
  const route = useRoute();
  const { projects, scripts, models, voices, deletedSpend, refresh } = useCatalogue();
  const [projectId, setProjectId] = useState<number | null>(null);
  const [remembered, setRemembered] = useState<number | null>(rememberedEpisode);
  const [switcher, setSwitcher] = useState(false);

  const routeId = route.params.id ? Number(route.params.id) : null;
  const routeScript = scripts.find((s) => s.id === routeId) ?? null;

  useEffect(() => {
    if (routeId === null) return;
    setRemembered(routeId);
    try {
      localStorage.setItem(EPISODE_KEY, String(routeId));
    } catch {
      // Not remembered across visits; still right for this one.
    }
  }, [routeId]);

  // Opening a script implies its project, so the menu and the media page
  // follow along without the selection having to be made twice.
  // A selection that no longer exists — a project just deleted — falls back to
  // the first one, or "Add a script" would post to the deleted project while
  // the picker showed another.
  // Without one open, the project of the episode last worked on comes first, so
  // the menu and the player pick up where you left off.
  const rememberedScript = scripts.find((s) => s.id === remembered) ?? null;
  useEffect(() => {
    if (routeScript) setProjectId(routeScript.project_id);
    else if (!projects.some((p) => p.id === projectId))
      setProjectId(rememberedScript?.project_id ?? projects[0]?.id ?? null);
  }, [routeScript, rememberedScript, projects, projectId]);

  const project = projects.find((p) => p.id === projectId) ?? null;
  const episodes = useMemo(
    () => scripts.filter((s) => s.project_id === projectId),
    [scripts, projectId],
  );
  // The episode in hand: the one open, else the one last opened if it is in
  // this project, else the project's newest.
  const episode =
    routeScript ??
    episodes.find((s) => s.id === remembered) ??
    episodes[episodes.length - 1] ??
    null;
  const episodeId = routeId ?? episode?.id ?? null;

  // The script's own model where it has one — not always the project's.
  const model = models.find((m) => m.model_id === (routeScript?.model_id ?? project?.model_id));
  // "Brian Robinson - Relatable Everyman": the name, without the blurb.
  const voiceName = (id: string | null | undefined) =>
    (id && voices.find((v) => v.voice_id === id)?.name.split(" - ")[0]) || null;
  const projectModel = models.find((m) => m.model_id === project?.model_id);
  const projectMeta = [
    projectModel?.label ?? project?.model_id.replace("eleven_", ""),
    voiceName(project?.voice_id),
  ]
    .filter(Boolean)
    .join(" · ");

  const state = useScript(episodeId);
  const { timeline, chunks, slots, cost, plan } = state;

  // Feed the transport whatever the episode's timeline holds, so pressing play
  // plays the episode rather than one clip. Only an episode that still exists:
  // a deleted one's clips may be gone from disk.
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

  const flagged = chunks.filter((c) =>
    c.takes.some(
      (t) =>
        t.in_cut &&
        !t.verify_accepted &&
        (t.verify_status === "suspect" || t.verify_status === "review"),
    ),
  ).length;
  const totalSpend = projects.reduce((sum, p) => sum + p.spend_micros, 0) + deletedSpend;

  const chunkStarts = useMemo(
    () => (timeline?.entries ?? []).filter((e) => e.kind === "narration").map((e) => e.start_s),
    [timeline],
  );
  const markers = useMemo(
    () =>
      (timeline?.entries ?? [])
        .filter((e) => e.kind !== "narration")
        .map((e) => ({ at: e.start_s, planned: !e.generated })),
    [timeline],
  );

  return (
    <div className="layout">
      <Rail
        projects={projects}
        project={project}
        projectMeta={projectMeta}
        onProject={setProjectId}
        episodes={episodes}
        episode={episode}
        totalSpend={totalSpend}
        flagged={flagged}
        effects={slots.length}
      />

      <div className="main">
        <Topbar
          pattern={route.pattern}
          project={project}
          episodes={episodes}
          episode={episode}
          onSearch={() => setSwitcher(true)}
        />

        <main className="page">
          {state.error && routeId !== null && (
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
              voices={voices}
              selected={projectId}
              onSelect={setProjectId}
              onChanged={() => void refresh()}
              episode={episode}
              timeline={timeline}
              chunks={chunks}
            />
          )}

          {route.pattern === "/script/:id" && routeId !== null && (
            <ScriptPage
              scriptId={routeId}
              script={routeScript}
              project={project}
              model={model}
              voiceName={voiceName(project?.voice_id)}
              timeline={timeline}
              chunks={chunks}
              slots={slots}
              cost={cost}
              onRefresh={() => {
                void state.refresh();
                void refresh();
              }}
            />
          )}

          {route.pattern === "/script/:id/effects" && routeId !== null && (
            <EffectsPage
              scriptId={routeId}
              script={routeScript}
              slots={slots}
              chunks={chunks}
              timeline={timeline}
              onRefresh={() => void state.refresh()}
            />
          )}

          {route.pattern === "/script/:id/media" && (
            <MediaPage
              project={project}
              scriptId={routeId}
              scripts={episodes}
              timeline={timeline}
              slots={slots}
            />
          )}

          {route.pattern === "/script/:id/plan" && routeId !== null && (
            <PlanPage scriptId={routeId} script={routeScript} plan={plan} />
          )}
          {route.pattern === "/script/:id/publish" && routeId !== null && (
            <PublishPage scriptId={routeId} script={routeScript} />
          )}

          {route.pattern === "/cast" && (
            <CastPage
              project={project}
              models={models}
              voices={voices}
              scripts={episodes}
              onChanged={() => void refresh()}
            />
          )}

          {route.pattern === "/costs" && (
            <CostsPage
              projects={projects}
              scripts={scripts}
              selected={projectId}
              onChanged={() => void refresh()}
            />
          )}

          {route.pattern === null && (
            <div className="panel">
              <div className="empty">
                <strong>No such page</strong>
                <Link to="/">Back to projects →</Link>
              </div>
            </div>
          )}
        </main>

        <Transport
          title={episode?.title}
          subtitle={[project?.name, voiceName(project?.voice_id)].filter(Boolean).join(" · ")}
          chunkStarts={chunkStarts}
          markers={markers}
        />
      </div>

      <QuickSwitcher
        open={switcher}
        onOpenChange={setSwitcher}
        projects={projects}
        scripts={scripts}
        episode={episode}
        onProject={setProjectId}
      />
    </div>
  );
}
