import { useCallback, useEffect, useState } from "react";
import { api, usd } from "../api";
import { ChevronDownIcon } from "../components/Icon";
import { SpendPerDay } from "../components/SpendPerDay";
import { initial, resetDay } from "../components/Rail";
import { Link, scriptPath } from "../router";
import { Loadable, Notice } from "../ui/feedback";
import { PageHeader } from "../ui/layout";
import type { Cost, Credits, Deleted, Project, Reconciled, ScriptSummary, Spend } from "../types";

const KIND_LABEL: Record<string, string> = {
  generation: "🎙️ Narration",
  effect: "✨ Effects",
  suggestion: "🪄 Suggestions",
  probe: "🔬 Probes",
  correction: "🩹 Corrections",
};

/** Spend across every project, and the per-operation breakdown behind it.
 *
 *  This page fetches a cost rollup per script, which on a real account is a
 *  dozen requests — and it used to render nothing at all while they were in
 *  flight, then fail silently if one of them broke. A blank cost page is the
 *  worst possible blank page: it reads as "you have spent nothing".
 */
export function CostsPage({
  projects,
  scripts,
  credits,
  spend,
  selected,
  onChanged,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
  /** What is left on the ElevenLabs subscription; absent without a key. */
  credits?: Credits | null;
  /** This month's spend by day, and last month's total. */
  spend?: Spend | null;
  /** The project in hand, whose episodes are listed first. */
  selected: number | null;
  /** After a restore, so the rest of the app sees it again. */
  onChanged?: () => void;
}) {
  // Deleted projects and episodes: hidden everywhere else, but what they cost
  // is still spend, and a total that dropped it would be wrong.
  const [deleted, setDeleted] = useState<Deleted | null>(null);
  const loadDeleted = useCallback(() => {
    api
      .deleted()
      .then(setDeleted)
      .catch(() => setDeleted(null));
  }, []);
  useEffect(loadDeleted, [loadDeleted]);

  const [restoreError, setRestoreError] = useState<string | null>(null);
  async function restore(kind: "project" | "script", id: number) {
    setRestoreError(null);
    try {
      await (kind === "project" ? api.restoreProject(id) : api.restoreScript(id));
    } catch (e) {
      setRestoreError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      loadDeleted();
      onChanged?.();
    }
  }
  const [byScript, setByScript] = useState<Record<number, Cost>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Only a choice made here: until then the list follows the project in hand,
  // which may not have loaded yet when this first renders.
  const [filter, setFilter] = useState<number | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all(scripts.map(async (s) => [s.id, await api.cost(s.id)] as const))
      .then((pairs) => {
        if (!cancelled) setByScript(Object.fromEntries(pairs));
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scripts]);

  useEffect(load, [load]);

  const deletedProjects = deleted?.projects ?? [];
  const deletedSpend = deletedProjects.reduce((sum, p) => sum + p.spend_micros, 0);
  const total = projects.reduce((sum, p) => sum + p.spend_micros, 0) + deletedSpend;
  const deletedEpisodes = (deleted?.scripts ?? []).filter((s) => !s.with_project);
  // The deleted projects' waste too, or "of that" would stop matching its total.
  const wasted =
    projects.reduce((sum, p) => sum + Math.round((p.spend_micros * p.waste_pct) / 100), 0) +
    deletedProjects.reduce((sum, p) => sum + p.wasted_micros, 0);
  // The ledger's own month when it is in, deleted projects' charges included.
  const month = spend?.month_micros ?? projects.reduce((sum, p) => sum + p.month_micros, 0);
  const monthName = new Date().toLocaleString("en-GB", { month: "long" });
  const lastMonthName = spend
    ? new Date(`${spend.last_month_start}T00:00:00Z`).toLocaleString("en-GB", { month: "long", timeZone: "UTC" })
    : "";

  // Per finished minute, across the episodes that have finished audio: each
  // one's spend over its minutes, the minutes being what its own rate implies.
  const finished = Object.values(byScript).filter((c) => c.cost_per_minute_micros);
  const minutes = finished.reduce((sum, c) => sum + c.cost_micros / c.cost_per_minute_micros!, 0);
  const perMinute = minutes ? finished.reduce((sum, c) => sum + c.cost_micros, 0) / minutes : 0;

  const shownProject = filter ?? selected ?? projects[0]?.id ?? null;
  const episodes = scripts.filter((s) => s.project_id === shownProject);
  const spent = [...episodes].reverse().filter((s) => byScript[s.id]?.by_kind.length);
  const unspent = episodes.filter((s) => byScript[s.id] && !byScript[s.id]!.by_kind.length);

  return (
    <>
      <PageHeader
        kicker="💰 Every dollar and credit, attributed to what bought it"
        title="Costs"
        actions={
          <div className="cost-total">
            <span className="cost-total-figure mono">{usd(total, 4)}</span>
            <span className="dim">
              all time · {projects.length} project{projects.length === 1 ? "" : "s"}
              {deletedProjects.length ? ` + ${deletedProjects.length} deleted` : ""}
            </span>
          </div>
        }
      />

      <div className="stat-tiles">
        <div className="stat-tile">
          <span className="stat-tile-label">📅 {monthName} so far</span>
          <span className="stat-tile-figure mono">{usd(month, 2)}</span>
          <span className="stat-tile-note">
            {spend && spend.last_month_micros > 0 ? (
              <>
                {month >= spend.last_month_micros ? "↑" : "↓"}{" "}
                {Math.abs(Math.round(((month - spend.last_month_micros) / spend.last_month_micros) * 100))}% on{" "}
                {lastMonthName}'s <span className="mono">{usd(spend.last_month_micros, 2)}</span>
              </>
            ) : (
              "what each project's cap is measured against"
            )}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile-label">🗑️ Wasted on re-rolls</span>
          <span className="stat-tile-figure mono">{usd(wasted, 2)}</span>
          <span className="stat-tile-note">
            {total ? `${((wasted / total) * 100).toFixed(1)}% of everything spent` : "nothing spent yet"}
          </span>
        </div>
        {credits?.available && (
          <div className="stat-tile">
            <span className="stat-tile-label">🔋 ElevenLabs credits left</span>
            <span className="stat-tile-figure mono">{credits.remaining.toLocaleString()}</span>
            <div
              className={credits.limit && credits.remaining / credits.limit < 0.1 ? "meter warn" : "meter plain"}
              aria-hidden
            >
              <div style={{ width: `${credits.limit ? (credits.remaining / credits.limit) * 100 : 0}%` }} />
            </div>
            <span className="stat-tile-note">
              of {credits.limit.toLocaleString()} this billing month
              {credits.resets_at ? ` · resets ${resetDay(credits.resets_at)}` : ""}
            </span>
          </div>
        )}
        {perMinute > 0 && (
          <div className="stat-tile">
            <span className="stat-tile-label">⏱️ Per finished minute</span>
            <span className="stat-tile-figure mono">{usd(Math.round(perMinute))}</span>
            <span className="stat-tile-note">
              across {Math.round(minutes).toLocaleString()} minutes of finished audio
            </span>
          </div>
        )}
      </div>

      <div className="row">
        <div className="grow">
          {spend && spend.days.length > 0 && <SpendPerDay spend={spend} projects={projects} />}

          <section className="panel" aria-labelledby="proj-h">
            <div className="card-head">
              <h2 id="proj-h" className="card-title">
                🗂️ By project
              </h2>
            </div>
            <div className="table-scroll">
              <table className="cost-projects">
                <caption className="sr-only">Spend by project, with waste and monthly cap</caption>
                <thead>
                  <tr>
                    <th scope="col">Project</th>
                    <th scope="col" className="num">
                      All time
                    </th>
                    <th scope="col" className="num">
                      {monthName}
                    </th>
                    <th scope="col" className="num">
                      Waste
                    </th>
                    <th scope="col" className="cap-col">
                      Monthly cap
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {projects.map((p) => {
                    const tone = p.cap_used_pct >= 100 ? "danger" : p.cap_used_pct >= 80 ? "warn" : "";
                    return (
                      <tr key={p.id}>
                        <td className="cost-project-name">
                          <span className="icon-tile sz-20" aria-hidden>
                            {initial(p.name)}
                          </span>{" "}
                          {p.name}
                        </td>
                        <td className="num mono">{usd(p.spend_micros)}</td>
                        <td className="num mono dim">{usd(p.month_micros, 2)}</td>
                        <td className={p.waste_pct >= 25 ? "num mono warn" : "num mono"}>
                          {p.spend_micros ? `${p.waste_pct}%` : "—"}
                        </td>
                        <td>
                          {p.cap_micros !== null ? (
                            <div className="cap-cell">
                              <div className={`meter ${tone}`} aria-hidden>
                                <div style={{ width: `${Math.min(100, p.cap_used_pct)}%` }} />
                              </div>
                              <span className="mono">
                                {p.cap_used_pct}% of {usd(p.cap_micros, 2).replace(/\.00$/, "")}
                              </span>
                            </div>
                          ) : (
                            <span className="faint">No cap</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {deletedProjects.map((p) => (
                    <tr key={`deleted-${p.id}`} className="deleted-row">
                      <td className="cost-project-name">
                        {p.name} <span className="pill quiet small-pill">deleted</span>
                      </td>
                      <td className="num mono">{usd(p.spend_micros)}</td>
                      <td className="num mono dim">—</td>
                      <td className="num mono">
                        {p.spend_micros ? `${Math.round((p.wasted_micros / p.spend_micros) * 100)}%` : "—"}
                      </td>
                      <td className="faint">Still counted in every total</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note flush">
              Waste is spend on takes that never made the cut — the number that says whether the
              direction is working, not the voice.
            </p>
          </section>

          <section className="panel" aria-labelledby="ep-h">
            <div className="card-head">
              <h2 id="ep-h" className="card-title">
                🧾 By episode, and what bought it
              </h2>
              {projects.length > 1 && (
                <label className="find-sort">
                  Project
                  <select value={shownProject ?? ""} onChange={(e) => setFilter(Number(e.target.value))}>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <Loadable loading={loading} error={error} rows={4} onRetry={load}>
              {spent.length === 0 ? (
                <Notice>Nothing generated in this project yet.</Notice>
              ) : (
                <div className="cost-episodes">
                  {spent.map((script, index) => (
                    <EpisodeCost key={script.id} script={script} cost={byScript[script.id]!} open={index === 0} />
                  ))}
                </div>
              )}
              {unspent.length > 0 && (
                <p className="note flush">
                  {unspent.map((s) => s.title).join(", ")} {unspent.length === 1 ? "has" : "have"} a
                  script but nothing generated yet.
                </p>
              )}
            </Loadable>
          </section>
        </div>

        <div className="side">
          <section className="panel" aria-labelledby="acct-h">
            <div className="card-head">
              <h2 id="acct-h" className="card-title">
                🧮 Account
              </h2>
            </div>
            <dl className="account">
              <div className="account-total">
                <dt>Total spend</dt>
                <dd className="mono">{usd(total, 4)}</dd>
              </div>
              <div className={wasted ? "warn" : undefined}>
                <dt>Of that, wasted</dt>
                <dd className="mono">{usd(wasted, 4)}</dd>
              </div>
              <div>
                <dt>Projects</dt>
                <dd>
                  {projects.length}
                  {deletedProjects.length > 0 && <span className="faint"> + {deletedProjects.length} deleted</span>}
                </dd>
              </div>
              <div>
                <dt>Episodes</dt>
                <dd>
                  {scripts.length}
                  {deletedEpisodes.length > 0 && <span className="faint"> + {deletedEpisodes.length} deleted</span>}
                </dd>
              </div>
            </dl>
            {credits?.available && <MatchesElevenLabs />}
            <p className="note flush account-note">
              🔒 Every figure comes from an append-only record, so a re-roll can never quietly
              disappear from it.
            </p>
          </section>

          {/* Outside the card: when the row that failed was the last one — restored
              meanwhile in another tab — the refresh removes the card, and the
              message has to outlive it. */}
          {restoreError && <Notice tone="error">{restoreError}</Notice>}
          {deleted && (deletedProjects.length > 0 || deletedEpisodes.length > 0) && (
            <section className="panel" aria-labelledby="del-h">
              <div className="card-head">
                <h2 id="del-h" className="card-title">
                  ♻️ Deleted
                </h2>
              </div>
              <div className="deleted-list">
                {deletedProjects.map((p) => (
                  <div key={`p${p.id}`} className="deleted-item">
                    <span>
                      <strong>{p.name}</strong>
                      <span className="faint">
                        project · <span className="mono">{usd(p.spend_micros)}</span>
                      </span>
                    </span>
                    <button className="small" onClick={() => void restore("project", p.id)}>
                      Restore
                    </button>
                  </div>
                ))}
                {deletedEpisodes.map((e) => (
                  <div key={`s${e.id}`} className="deleted-item">
                    <span>
                      <strong>{e.title}</strong>
                      <span className="faint">
                        episode · <span className="mono">{usd(e.spend_micros)}</span>
                      </span>
                    </span>
                    <button className="small" onClick={() => void restore("script", e.id)}>
                      Restore
                    </button>
                  </div>
                ))}
              </div>
              <p className="note flush">
                Deleting hides a project or episode and stops it spending. What it cost stays counted
                — in the total above, and against its project's monthly cap.
              </p>
            </section>
          )}
        </div>
      </div>
    </>
  );
}

/** Whether the ledger agrees with what ElevenLabs says it billed, and a check
 *  that costs nothing: it reads the account's usage figures. */
function MatchesElevenLabs() {
  const [result, setResult] = useState<Reconciled | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .latestReconcile()
      .then(setResult)
      .catch(() => undefined);
  }, []);

  async function check() {
    setChecking(true);
    setError(null);
    try {
      setResult(await api.reconcile());
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setChecking(false);
    }
  }

  const checked = result ? new Date(result.checked_at) : null;
  return (
    <div className="reconcile">
      {result && (
        <>
          <span className={result.within_threshold ? "reconcile-verdict ok" : "reconcile-verdict warn"}>
            {result.within_threshold ? "✓ Matches ElevenLabs" : "⚠ Doesn't match ElevenLabs"}
          </span>
          <span className="faint">
            {result.within_threshold
              ? `${result.local_chars.toLocaleString()} characters on both sides`
              : result.verdict}{" "}
            · last 7 days, checked{" "}
            {checked!.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
          </span>
          {!result.within_threshold && result.note && <span className="faint">{result.note}</span>}
        </>
      )}
      <button type="button" className="small" disabled={checking} onClick={() => void check()}>
        {checking ? "Checking…" : result ? "Check again — free" : "Check against ElevenLabs — free"}
      </button>
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/** One episode's spend, by what bought it. */
function EpisodeCost({ script, cost, open: initiallyOpen }: { script: ScriptSummary; cost: Cost; open: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div className="cost-episode">
      <button
        type="button"
        className="cost-episode-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDownIcon size={14} className="chev" />
        <strong className="cost-episode-title">{script.title}</strong>
        {cost.cost_per_minute_micros ? (
          <span className="faint cost-episode-rate">
            <span className="mono">{usd(cost.cost_per_minute_micros)}</span>/min
          </span>
        ) : (
          <span className="cost-episode-rate" />
        )}
        <strong className="mono">{usd(cost.cost_micros)}</strong>
      </button>
      {open && (
        <div className="cost-episode-body">
          <div className="table-scroll">
            <table className="cost-ops">
              <caption className="sr-only">Spend on {script.title} by operation</caption>
              <thead>
                <tr>
                  <th scope="col">Operation</th>
                  <th scope="col" className="num">
                    Units
                  </th>
                  <th scope="col" className="num">
                    Spend
                  </th>
                  <th scope="col" className="num">
                    Credits
                  </th>
                </tr>
              </thead>
              <tbody>
                {cost.by_kind.map((k) => (
                  <tr key={`${k.kind}-${k.unit_kind}`}>
                    <td>{KIND_LABEL[k.kind] ?? k.kind}</td>
                    <td className="num mono dim">{k.units_display}</td>
                    <td className="num mono">{usd(k.cost_micros)}</td>
                    <td className="num mono dim">
                      {k.credits ? `${Math.round(k.credits).toLocaleString()} cr` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Link className="link-quiet" to={scriptPath(script.id)}>
            Open {script.title} →
          </Link>
        </div>
      )}
    </div>
  );
}
