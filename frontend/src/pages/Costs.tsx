import { useCallback, useEffect, useState } from "react";
import { api, usd } from "../api";
import { Link, scriptPath } from "../router";
import { Badge, Loadable, Notice, Stat } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import type { Cost, KindTotal, Project, ScriptSummary } from "../types";

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
}: {
  projects: Project[];
  scripts: ScriptSummary[];
}) {
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

  const total = projects.reduce((sum, p) => sum + p.spend_micros, 0);
  const wasted = projects.reduce(
    (sum, p) => sum + Math.round((p.spend_micros * p.waste_pct) / 100),
    0,
  );

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
