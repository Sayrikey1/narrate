import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { DownloadIcon } from "../components/Icon";
import * as transport from "../transport";
import { DocumentView } from "../ui/DocumentView";
import { Badge, Empty, Notice, Stat } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable } from "../ui/Table";
import { TabPanel, Tabs } from "../ui/Tabs";
import { formatClock, parseStamp } from "../time";
import type { Publish, ScriptSummary, TitleCandidate } from "../types";

/** The publish pack: what goes in the box the video ships in.
 *
 *  `plan.md` is for cutting the episode; this is for uploading it. The
 *  **Packaging** tab is where the decisions are made — choosing a title, choosing
 *  a thumbnail brief, seeing which chapter rules are broken — and every fact in
 *  it comes from the endpoint's data twin rather than from re-parsing the
 *  markdown beside it. Deriving the same fact twice is how two answers to one
 *  question happen.
 *
 *  Clicking a chapter row seeks the transport there, which is the fastest way to
 *  check that a chapter actually lands where its name says it does.
 */
export function PublishPage({
  scriptId,
  script,
}: {
  scriptId: number;
  script: ScriptSummary | null;
}) {
  const [tab, setTab] = useState<"document" | "packaging">("document");
  const [pack, setPack] = useState<Publish | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPack(await api.publish(scriptId));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [scriptId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <>
        <Head script={script} />
        <Notice tone="error" title="Could not load the publish pack">
          {error}
        </Notice>
      </>
    );
  }

  if (!pack) {
    return (
      <>
        <Head script={script} />
        <Card>
          <Empty title="Nothing to publish yet">
            Ingest a script and the pack fills in. Chapters and the retention
            target need no API key at all.
          </Empty>
        </Card>
      </>
    );
  }

  return (
    <>
      <Head script={script} pack={pack} />

      <Tabs label="How to read the pack" tabs={TABS} active={tab} onChange={setTab} />

      <TabPanel id={tab}>
        {tab === "document" ? (
          <DocumentView
            source={pack.markdown}
            filename="publish.md"
            onRowActivate={(cells) => seekTo(cells)}
            aside={<Summary pack={pack} />}
          />
        ) : (
          <Packaging scriptId={scriptId} pack={pack} onChange={load} />
        )}
      </TabPanel>
    </>
  );
}

const TABS = [
  { id: "document" as const, label: "Document" },
  { id: "packaging" as const, label: "Packaging" },
];

function Head({ script, pack }: { script: ScriptSummary | null; pack?: Publish }) {
  return (
    <PageHeader
      title="Publish pack"
      subtitle={
        pack
          ? `${pack.data.title.chosen || script?.title || ""} — chapters, title, tags and the thumbnail brief`
          : (script?.title ?? "")
      }
      actions={
        pack ? (
          <a
            className="download"
            href={URL.createObjectURL(new Blob([pack.data.chapters.text], { type: "text/plain" }))}
            download="chapters.txt"
          >
            <DownloadIcon /> chapters .txt
          </a>
        ) : undefined
      }
    />
  );
}

/** A chapter row seeks the playhead, so a name can be checked against the audio
 *  it claims to describe. */
function seekTo(cells: string[]): void {
  for (const cell of cells) {
    const seconds = parseStamp(cell);
    if (seconds === null) continue;
    transport.seek(seconds);
    return;
  }
}

function Summary({ pack }: { pack: Publish }) {
  const { chapters, retention, tags } = pack.data;
  return (
    <>
      <Stat label="Runtime" value={formatClock(pack.data.runtime_s)} />
      <Stat
        label="Chapters"
        value={String(chapters.entries.length)}
        tone={chapters.usable ? "ok" : "warn"}
      />
      {retention.good_pct > 0 && (
        <>
          <Stat
            label="Target average view"
            value={`${retention.good_pct}%`}
            tone={retention.extrapolated ? "warn" : undefined}
          />
          <Stat label="Hold for" value={formatClock(retention.good_hold_s)} />
        </>
      )}
      <Stat label="Tags" value={String(tags.length)} />
    </>
  );
}

function Packaging({
  scriptId,
  pack,
  onChange,
}: {
  scriptId: number;
  pack: Publish;
  onChange: () => void;
}) {
  const { chapters, title, thumbnail, briefs, retention } = pack.data;

  return (
    <Split
      main={
        <>
          <Card title="Chapters">
            {chapters.problems.map((problem) => (
              <Notice key={problem} tone="warn" title="YouTube would not accept this">
                {problem}
              </Notice>
            ))}
            {chapters.entries.length > 0 ? (
              <DataTable
                rows={chapters.entries}
                rowKey={(row) => `${row.stamp}-${row.title}`}
                columns={[
                  { header: "at", cell: (row) => row.stamp },
                  { header: "chapter", cell: (row) => row.title },
                  { header: "chunk", align: "num", cell: (row) => row.chunk_ordinal ?? "—" },
                ]}
              />
            ) : (
              <Empty title="No chapters named">
                Add a <code>## Heading</code>, a <code>[CHAPTER: ...]</code> marker,
                or a name on an anchor — <code>[@ 03:00 The Employee Trap]</code> —
                then re-chunk.
              </Empty>
            )}
            {chapters.usable && (
              <p className="hint">
                Times are measured from the exported audio, not from the script's
                anchors. Click a row to hear it.
              </p>
            )}
          </Card>

          <Card title="Titles">
            {title.chosen ? (
              <p>
                <strong>{title.chosen}</strong>{" "}
                {title.over_length && <Badge tone="warn">will be cut off</Badge>}
              </p>
            ) : (
              <Empty title="No title chosen">
                Generate candidates with <code>narrate publish draft --go</code>.
              </Empty>
            )}
            {title.candidates.length > 0 && (
              <TitleTable
                scriptId={scriptId}
                candidates={title.candidates}
                onChange={onChange}
              />
            )}
          </Card>
        </>
      }
      aside={
        <>
          <Card title="Thumbnail">
            {thumbnail ? (
              <dl className="detail">
                <dt>Composition</dt>
                <dd>{thumbnail.archetype}</dd>
                <dt>Text on image</dt>
                <dd>
                  <strong>{thumbnail.overlay_text}</strong>{" "}
                  <Badge tone={thumbnail.word_count <= 5 ? "ok" : "warn"}>
                    {thumbnail.word_count} words
                  </Badge>
                </dd>
                <dt>Subject</dt>
                <dd>{thumbnail.subject}</dd>
                <dt>Contrast</dt>
                <dd>{thumbnail.contrast}</dd>
                <dt>Leans on</dt>
                <dd>{thumbnail.principles}</dd>
              </dl>
            ) : (
              <Empty title="No brief yet">
                <code>narrate publish brief --go</code>. No image is generated — the
                brief is the deliverable.
              </Empty>
            )}
            {briefs.length > 1 && (
              <BriefList scriptId={scriptId} briefs={briefs} onChange={onChange} />
            )}
          </Card>

          {retention.good_pct > 0 && (
            <Card title="Retention">
              <p>
                A {formatClock(pack.data.runtime_s)} episode should hold{" "}
                <strong>{retention.good_pct}%</strong> to be doing well, and{" "}
                <strong>{retention.great_pct}%</strong> to be doing very well — that
                is <strong>{formatClock(retention.good_hold_s)}</strong> and{" "}
                <strong>{formatClock(retention.great_hold_s)}</strong> of watch time.
              </p>
              {retention.extrapolated && (
                <Notice tone="warn" title="Extrapolated">
                  Outside the published 8 to 120 minute range, so these are
                  extrapolated rather than quoted.
                </Notice>
              )}
            </Card>
          )}
        </>
      }
    />
  );
}

function TitleTable({
  scriptId,
  candidates,
  onChange,
}: {
  scriptId: number;
  candidates: TitleCandidate[];
  onChange: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);

  const choose = async (id: number) => {
    setBusy(id);
    try {
      await api.acceptTitle(scriptId, id);
      onChange();
    } finally {
      setBusy(null);
    }
  };

  return (
    <DataTable
      rows={candidates}
      rowKey={(row) => row.id}
      columns={[
        { header: "formula", cell: (row) => row.formula || "—" },
        {
          header: "chars",
          align: "num",
          cell: (row) =>
            row.over_length ? <Badge tone="warn">{row.text.length}</Badge> : row.text.length,
        },
        { header: "title", cell: (row) => row.text },
        { header: "why", cell: (row) => row.rationale || "—" },
        {
          header: "",
          cell: (row) =>
            row.accepted ? (
              <Badge tone="ok">chosen</Badge>
            ) : (
              <button
                type="button"
                className="ghost"
                disabled={busy !== null}
                onClick={() => void choose(row.id)}
              >
                {busy === row.id ? "choosing…" : "choose"}
              </button>
            ),
        },
      ]}
    />
  );
}

function BriefList({
  scriptId,
  briefs,
  onChange,
}: {
  scriptId: number;
  briefs: Publish["data"]["briefs"];
  onChange: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);

  const choose = async (id: number) => {
    setBusy(id);
    try {
      await api.chooseBrief(scriptId, id);
      onChange();
    } finally {
      setBusy(null);
    }
  };

  return (
    <ul className="brief-list">
      {briefs.map((brief) => (
        <li key={brief.id}>
          <span>
            {brief.archetype} — <strong>{brief.overlay_text}</strong>
          </span>
          {brief.accepted ? (
            <Badge tone="ok">chosen</Badge>
          ) : (
            <button
              type="button"
              className="ghost"
              disabled={busy !== null}
              onClick={() => void choose(brief.id)}
            >
              {busy === brief.id ? "choosing…" : "choose"}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
