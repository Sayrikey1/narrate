import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { ChunkRow, Cost, Model, Plan, Project, ScriptSummary, Slot, Timeline } from "./types";

/** One place that knows how to load a script's state, so every page shows the
 *  same numbers and a refresh after generating updates all of them at once. */
export function useScript(scriptId: number | null) {
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [chunks, setChunks] = useState<ChunkRow[]>([]);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [cost, setCost] = useState<Cost | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (scriptId === null) return;
    setLoading(true);
    try {
      const [tl, ch, sl, co, pl] = await Promise.all([
        api.timeline(scriptId),
        api.chunks(scriptId),
        api.slots(scriptId),
        api.cost(scriptId),
        api.plan(scriptId),
      ]);
      setTimeline(tl);
      setChunks(ch);
      setSlots(sl);
      setCost(co);
      setPlan(pl);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { timeline, chunks, slots, cost, plan, error, loading, refresh };
}

/** Projects, scripts and models — the shell's context, shared by every page. */
export function useCatalogue() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [scripts, setScripts] = useState<ScriptSummary[]>([]);
  const [models, setModels] = useState<Model[]>([]);

  const refresh = useCallback(async () => {
    const [p, s] = await Promise.all([api.projects(), api.scripts()]);
    setProjects(p);
    setScripts(s);
  }, []);

  useEffect(() => {
    void refresh();
    api.models().then(setModels).catch(() => undefined);
  }, [refresh]);

  return { projects, scripts, models, refresh };
}
