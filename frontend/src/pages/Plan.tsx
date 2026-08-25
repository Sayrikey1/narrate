import { useMemo, useState } from "react";
import { api, usd } from "../api";
import { DownloadIcon } from "../components/Icon";
import * as transport from "../transport";
import { DocumentView } from "../ui/DocumentView";
import { Badge, Empty, Notice, Stat } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable } from "../ui/Table";
import { TabPanel, Tabs } from "../ui/Tabs";
import { formatClock } from "../time";
import type { Plan, PlanEntry, ScriptSummary } from "../types";

/** The editing plan, as a document you can interrogate.
 *
 *  This page used to be a `<pre>` block: the running order — the reason the
 *  document exists — arrived as a wall of pipes and dashes. It now renders as a
 *  real table you can sort, and clicking a row seeks the transport to that
 *  moment, so "what is happening at 1:14" is one click rather than a mental
 *  conversion from a timecode.
 *
 *  The **Analysis** tab answers the questions the prose cannot: which cues are
 *  still missing, which anchors drifted furthest, where the runtime actually
 *  went. Those come from the endpoint's data twin, not from re-parsing the
 *  markdown — deriving the same facts a second way is how two answers to one
 *  question happen.
 */
export function PlanPage({
  scriptId,
  script,
  plan,
}: {
  scriptId: number;
  script: ScriptSummary | null;
  plan: Plan | null;
}) {
  const [tab, setTab] = useState<"document" | "analysis">("document");

  if (!plan?.markdown) {
    return (
      <>
        <Head scriptId={scriptId} script={script} />
        <Card>
          <Empty title="No plan yet">
            Generate the script and the plan fills in — every take, every cue, and
            where each one sits on the timeline.
          </Empty>
        </Card>
      </>
    );
  }

  return (
    <>
      <Head scriptId={scriptId} script={script} />

      <Tabs
        label="How to read the plan"
        tabs={TABS}
        active={tab}
        onChange={setTab}
      />

      <TabPanel id={tab}>
        {tab === "document" ? (
          <DocumentView
            source={plan.markdown}
            filename="plan.md"
            onRowActivate={(cells) => seekTo(cells)}
            aside={<Summary plan={plan} />}
          />
        ) : (
          <Analysis plan={plan} />
        )}
      </TabPanel>
    </>
  );
}

const TABS = [
  { id: "document" as const, label: "Document" },
  { id: "analysis" as const, label: "Analysis" },
];

function Head({ scriptId, script }: { scriptId: number; script: ScriptSummary | null }) {
  return (
    <PageHeader
      title="Editing plan"
      subtitle={`${script?.title ?? ""} — what plays when, and where a cue still belongs`}
      actions={
        <>
          <a className="download" href={api.scriptDownloadUrl(scriptId, "md")} download>
            <DownloadIcon /> script .md
          </a>
          <a className="download" href={api.scriptDownloadUrl(scriptId, "txt")} download>
            <DownloadIcon /> script .txt
          </a>
        </>
      }
    />
  );
}

/** Clicking a row in the running order moves the playhead there.
 *
 *  The first cell that parses as a timecode wins, so this keeps working if the
 *  plan's column order changes — matching on position would break silently the
 *  first time a column is inserted. */
function seekTo(cells: string[]): void {
  for (const cell of cells) {
    const match = /^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$/.exec(cell.trim());
    if (!match) continue;
    const [, h, m, s] = match;
    transport.seek(Number(h) * 3600 + Number(m) * 60 + Number(s));
    return;
  }
}

function Summary({ plan }: { plan: Plan }) {
  const stats = useMemo(() => analyse(plan.data.entries), [plan]);
  return (
    <Card title="At a glance" className="doc-summary">
      <Stat label="Runtime" value={formatClock(plan.data.runtime_s)} />
      <Stat label="Narration" value={`${stats.narration} chunk(s)`} />
      <Stat label="Effects" value={`${stats.effects} placed`} />
      {stats.planned > 0 && (
        <Stat label="Not generated" value={`${stats.planned} cue(s)`} tone="warn" />
      )}
      <Stat label="Spent" value={usd(plan.data.cost_micros, 2)} total />
      {!plan.data.complete && (
        <Notice tone="warn">
          Some pieces are still missing, so the timings after the first gap are
          provisional.
        </Notice>
      )}
    </Card>
  );
}

function Analysis({ plan }: { plan: Plan }) {
  const stats = useMemo(() => analyse(plan.data.entries), [plan]);
  const runtime = plan.data.runtime_s || 1;
  const length = (entry: PlanEntry) => entry.end_s - entry.start_s;

  return (
    <Split
      main={
        <>
          <Card title="Where the runtime went">
            <Lane
              label="Narration"
              seconds={stats.narrationSeconds}
              runtime={runtime}
              tone="narration"
            />
            <Lane
              label="Gaps between chunks"
              seconds={Math.max(0, runtime - stats.narrationSeconds)}
              runtime={runtime}
              tone="planned"
            />
            <Lane
              label="Effects (overlaid, not added)"
              seconds={stats.effectSeconds}
              runtime={runtime}
              tone="effect"
            />
            <Notice>
              Effects are overlays, so their duration does not extend the runtime
              — it is how much of the episode has something under it.
            </Notice>
          </Card>

          <Card title="Longest and shortest">
            <DataTable
              compact
              rowKey={(row) => row.label}
              columns={[
                { header: "", cell: (row) => row.label },
                { header: "", cell: (row) => <span className="dim">{row.detail}</span> },
                { header: "", align: "num", cell: (row) => <span className="mono">{row.value}</span> },
              ]}
              rows={[
                ...(stats.longest
                  ? [
                      {
                        label: "Longest chunk",
                        detail: `${stats.longest.label.slice(0, 52)}…`,
                        value: `${length(stats.longest).toFixed(2)}s`,
                      },
                    ]
                  : []),
                ...(stats.shortest
                  ? [
                      {
                        label: "Shortest chunk",
                        detail: `${stats.shortest.label.slice(0, 52)}…`,
                        value: `${length(stats.shortest).toFixed(2)}s`,
                      },
                    ]
                  : []),
                {
                  label: "Cost per finished minute",
                  detail: "",
                  value: usd(Math.round(plan.data.cost_micros / (runtime / 60))),
                },
              ]}
            />
          </Card>
        </>
      }
      aside={
        <>
          {stats.drifted.length > 0 && (
            <Card title="Anchors that drifted">
              <Notice>
                A <code>[@ MM:SS]</code> marker is a target, not a command —
                speech duration cannot be dialled to a mark. These are the ones
                worth a second look.
              </Notice>
              <DataTable
                compact
                rowKey={(entry) => entry.index}
                rows={stats.drifted}
                columns={[
                  {
                    header: "Wanted",
                    cell: (e) => <span className="mono">{formatClock(e.target_s ?? 0)}</span>,
                  },
                  { header: "Landed", cell: (e) => <span className="mono">{e.start}</span> },
                  {
                    header: "Drift",
                    align: "num",
                    cell: (e) => (
                      <span className={Math.abs(e.drift_s ?? 0) > 10 ? "warn" : "dim"}>
                        {(e.drift_s ?? 0) > 0 ? "+" : ""}
                        {(e.drift_s ?? 0).toFixed(1)}s
                      </span>
                    ),
                  },
                ]}
              />
            </Card>
          )}

          {stats.missing.length > 0 && (
            <Card title="Still to generate">
              <ul className="md-list">
                {stats.missing.map((entry) => (
                  <li key={entry.index}>
                    <Badge tone="planned">{entry.start}</Badge> {entry.label}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {stats.drifted.length === 0 && stats.missing.length === 0 && (
            <Card>
              <Empty title="Nothing outstanding">
                Every cue is generated and every anchor landed close to its mark.
              </Empty>
            </Card>
          )}
        </>
      }
    />
  );
}


function Lane({
  label,
  seconds,
  runtime,
  tone,
}: {
  label: string;
  seconds: number;
  runtime: number;
  tone: string;
}) {
  const pct = Math.min(100, (seconds / runtime) * 100);
  return (
    <div className="doc-lane">
      <span className="dim">{label}</span>
      <div className="bar">
        <div style={{ width: `${pct}%`, background: `var(--${tone})` }} />
      </div>
      <span className="num mono">{formatClock(seconds)}</span>
    </div>
  );
}

/** Everything the analysis reports, derived once from the data twin. */
function analyse(entries: PlanEntry[]) {
  const narration = entries.filter((e) => e.kind === "narration");
  const effects = entries.filter((e) => e.kind === "effect");
  const planned = entries.filter((e) => e.kind === "planned" || !e.generated);
  const length = (e: PlanEntry) => e.end_s - e.start_s;

  const sorted = [...narration].sort((a, b) => length(a) - length(b));

  return {
    narration: narration.length,
    effects: effects.length,
    planned: planned.length,
    narrationSeconds: narration.reduce((sum, e) => sum + length(e), 0),
    effectSeconds: effects.reduce((sum, e) => sum + length(e), 0),
    longest: sorted.at(-1) ?? null,
    shortest: sorted[0] ?? null,
    // Worst first: the point is to see the outliers, not to read a list.
    drifted: entries
      .filter((e) => e.drift_s !== null && Math.abs(e.drift_s) >= 1)
      .sort((a, b) => Math.abs(b.drift_s ?? 0) - Math.abs(a.drift_s ?? 0)),
    missing: planned,
  };
}
