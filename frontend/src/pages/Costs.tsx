import { useCallback, useEffect, useState } from "react";
import { api, usd } from "../api";
import { Link, scriptPath } from "../router";
import { Badge, Loadable, Notice, Stat } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import type { Cost, Deleted, KindTotal, Project, ScriptSummary } from "../types";

const KIND_LABEL: Record<string, string> = {
  generation: "narration",
  effect: "effects",
  suggestion: "suggestions",
  probe: "probes",
  correction: "corrections",
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
  onChanged,
}: {
  projects: Project[];
  scripts: ScriptSummary[];
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

  const deletedSpend = (deleted?.projects ?? []).reduce((sum, p) => sum + p.spend_micros, 0);
  const total = projects.reduce((sum, p) => sum + p.spend_micros, 0) + deletedSpend;
  const deletedEpisodes = (deleted?.scripts ?? []).filter((s) => !s.with_project);
  // The deleted projects' waste too, or "of that" would stop matching its total.
  const wasted =
    projects.reduce((sum, p) => sum + Math.round((p.spend_micros * p.waste_pct) / 100), 0) +
    (deleted?.projects ?? []).reduce((sum, p) => sum + p.wasted_micros, 0);

  const projectColumns: readonly Column<Project>[] = [
    { header: "Project", cell: (p) => p.name },
    { header: "Spend", align: "num", cell: (p) => <span className="mono">{usd(p.spend_micros)}</span> },
    { header: "Waste", align: "num", cell: (p) => <span className="mono">{p.waste_pct}%</span> },
    {
      header: "Cap",
      cell: (p) =>
        p.cap_micros !== null ? (
          <Badge tone={p.cap_used_pct >= 80 ? "planned" : undefined}>{p.cap_used_pct}%</Badge>
        ) : (
          <span className="faint">—</span>
        ),
    },
  ];

  const kindColumns: readonly Column<KindTotal>[] = [
    { header: "Operation", cell: (k) => KIND_LABEL[k.kind] ?? k.kind },
    { header: "Units", align: "num", cell: (k) => <span className="faint mono">{k.units_display}</span> },
    { header: "Spend", align: "num", cell: (k) => <span className="mono">{usd(k.cost_micros)}</span> },
    {
      header: "Credits",
      align: "num",
      cell: (k) => (
        <span className="faint mono">
          {k.credits ? `${Math.round(k.credits).toLocaleString()} cr` : "—"}
        </span>
      ),
    },
  ];

  const withCosts = scripts.filter((s) => byScript[s.id]?.by_kind.length);

  return (
    <>
      <PageHeader
        title="Costs"
        subtitle="every dollar and credit, attributed to what bought it"
        actions={<span className="page-figure">{usd(total, 4)}</span>}
      />

      <Split
        main={
          <>
            <Card title="By project">
              <DataTable
                compact
                columns={projectColumns}
                rows={projects}
                rowKey={(p) => p.id}
                caption="Spend and waste by project"
                empty={{
                  title: "No projects yet",
                  body: "Spend appears here as soon as something is generated.",
                }}
              />
              <Notice>
                Waste is spend on takes that never made the cut — the number that
                says whether the direction is working, not the voice.
              </Notice>
            </Card>

            {/* Outside the card: when the row that failed was the last one — restored
                meanwhile in another tab — the refresh removes the card, and the
                message has to outlive it. */}
            {restoreError && <Notice tone="error">{restoreError}</Notice>}
            {deleted && (deleted.projects.length > 0 || deletedEpisodes.length > 0) && (
              <Card title="Deleted">
                <table className="compact">
                  <caption className="sr-only">Deleted projects and episodes</caption>
                  <tbody>
                    {deleted.projects.map((p) => (
                      <tr key={`p${p.id}`}>
                        <td>Project · {p.name}</td>
                        <td className="num mono">{usd(p.spend_micros)}</td>
                        <td>
                          <button className="small" onClick={() => void restore("project", p.id)}>
                            Restore
                          </button>
                        </td>
                      </tr>
                    ))}
                    {deletedEpisodes.map((e) => (
                      <tr key={`s${e.id}`}>
                        <td>Episode · {e.title}</td>
                        <td className="num mono">{usd(e.spend_micros)}</td>
                        <td>
                          <button className="small" onClick={() => void restore("script", e.id)}>
                            Restore
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Notice>
                  Deleting hides a project or episode and stops it spending. What it cost stays
                  counted — in the total above, and against its project's monthly cap.
                </Notice>
              </Card>
            )}

            <Card title="By script and operation">
              <Loadable loading={loading} error={error} rows={4} onRetry={load}>
                {withCosts.length === 0 ? (
                  <Notice>Nothing generated yet.</Notice>
                ) : (
                  withCosts.map((script) => {
                    const cost = byScript[script.id]!;
                    return (
                      <div key={script.id} className="cost-group">
                        <div className="group-head">
                          <strong>
                            <Link className="link-strong" to={scriptPath(script.id)}>
                              {script.title}
                            </Link>
                          </strong>
                          <span className="dim mono">{usd(cost.cost_micros)}</span>
                          {cost.cost_per_minute_micros ? (
                            <span className="faint">
                              {usd(cost.cost_per_minute_micros)}/min
                            </span>
                          ) : null}
                        </div>
                        <DataTable
                          compact
                          columns={kindColumns}
                          rows={cost.by_kind}
                          rowKey={(k) => `${k.kind}-${k.unit_kind}`}
                        />
                      </div>
                    );
                  })
                )}
              </Loadable>
            </Card>
          </>
        }
        aside={
          <Card title="Account">
            <Stat label="Total spend" value={usd(total, 4)} total />
            <Stat label="Of that, wasted" value={usd(wasted, 4)} tone={wasted ? "warn" : undefined} />
            <Stat label="Projects" value={String(projects.length)} />
            <Stat label="Scripts" value={String(scripts.length)} />
            <Notice>
              Every figure here comes from the append-only ledger, not from a
              running total — so it reproduces exactly, and a re-roll can never
              quietly disappear from it.
            </Notice>
          </Card>
        }
      />
    </>
  );
}
