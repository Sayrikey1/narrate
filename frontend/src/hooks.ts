import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  ActiveRun,
  ChunkRow,
  Cost,
  Credits,
  Model,
  Plan,
  Project,
  ScriptSummary,
  Slot,
  Spend,
  Timeline,
  Voice,
} from "./types";

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
  const [voices, setVoices] = useState<Voice[]>([]);
  // What deleted projects cost still counts, so the total the menu shows agrees
  // with the one on the Costs page.
  const [deletedSpend, setDeletedSpend] = useState(0);

  const refresh = useCallback(async () => {
    const [p, s] = await Promise.all([api.projects(), api.scripts()]);
    setProjects(p);
    setScripts(s);
    api
      .deleted()
      .then((d) => setDeletedSpend(d.projects.reduce((sum, x) => sum + x.spend_micros, 0)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    void refresh();
    api.models().then(setModels).catch(() => undefined);
    // Names for voice ids. Optional: without a key the list is empty and ids
    // are shown instead.
    api.voices().then(setVoices).catch(() => undefined);
  }, [refresh]);

  return { projects, scripts, models, voices, deletedSpend, refresh };
}

/** What is left on the ElevenLabs subscription. Asked again, past the server's
 *  minute-long cache, whenever the spend on record changes - after a run, which
 *  is exactly when the figure moves. */
export function useCredits(spend: number): Credits | null {
  const [credits, setCredits] = useState<Credits | null>(null);
  const first = useRef(true);

  useEffect(() => {
    const fresh = !first.current;
    first.current = false;
    let alive = true;
    api
      .credits(fresh)
      .then((c) => alive && setCredits(c))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [spend]);

  return credits;
}

/** What is running now, asked every few seconds while something is and less
 *  often when nothing is. `onFinished` runs when a run drops off the list, so
 *  the figures it changed are fetched again without a reload. */
export function useActiveRuns(onFinished: () => void): ActiveRun[] {
  const [runs, setRuns] = useState<ActiveRun[]>([]);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  const previous = useRef<string[]>([]);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    async function poll() {
      let next: ActiveRun[] = [];
      try {
        next = await api.activeRuns();
      } catch {
        // A server restarting: try again on the slow beat.
      }
      if (!alive) return;
      const keys = next.map((r) => `${r.script_id}:${r.phase}`);
      if (previous.current.some((key) => !keys.includes(key))) finished.current();
      previous.current = keys;
      setRuns(next);
      timer = window.setTimeout(poll, next.length ? 3000 : 10000);
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  return runs;
}

/** This month's spend by day and last month's total, fetched again when the
 *  spend on record changes. */
export function useSpend(spend: number): Spend | null {
  const [summary, setSummary] = useState<Spend | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .spend()
      .then((s) => alive && setSummary(s))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [spend]);
  return summary;
}
