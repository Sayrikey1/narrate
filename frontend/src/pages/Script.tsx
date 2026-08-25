import { useEffect, useState } from "react";
import { api, usd } from "../api";
import { ChunkList } from "../components/ChunkList";
import { CostPanel } from "../components/CostPanel";
import { DownloadIcon, SparkIcon } from "../components/Icon";
import { TimelineView } from "../components/Timeline";
import { TrackView } from "../components/TrackView";
import { Link, scriptPath } from "../router";
import { Empty, Notice } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import type {
  AudioFormatInfo,
  ChunkRow,
  Cost,
  ExportResult,
  Model,
  Project,
  ScriptSummary,
  Timeline,
} from "../types";

/** The working page: generate, watch the timeline, choose takes, export. */
export function ScriptPage({
  scriptId,
  script,
  project,
  model,
  timeline,
  chunks,
  cost,
  onRefresh,
}: {
  scriptId: number;
  script: ScriptSummary | null;
  project: Project | null;
  model: Model | undefined;
  timeline: Timeline | null;
  chunks: ChunkRow[];
  cost: Cost | null;
  onRefresh: () => void;
}) {
  const [formats, setFormats] = useState<AudioFormatInfo[]>([]);
  const [chosen, setChosen] = useState<string[]>(["wav", "mp3"]);
  const [withEffects, setWithEffects] = useState(true);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [exported, setExported] = useState<ExportResult | null>(null);
  const [detail, setDetail] = useState(false);

  useEffect(() => {
    api
      .formats()
      .then(setFormats)
      .catch(() => undefined);
  }, []);

  async function generate(dryRun: boolean) {
    setBusy(true);
    setError(null);
    setLog([]);
    try {
      const started = await api.generate(scriptId, {
        dry_run: dryRun,
        with_effects: withEffects,
      });
      const p = started.projection;
      setLog([
        `narration ${p.chunks} chunk(s), ${p.chars.toLocaleString()} chars — ${usd(p.speech_micros)}`,
        ...(p.effects
          ? [`effects   ${p.effects} cue(s), ${p.effect_seconds}s — ${usd(p.effect_micros)}`]
          : []),
        `total ${usd(p.total_micros)}`,
        "",
      ]);
      api.streamRun(
        started.run_key,
        (message) => setLog((prior) => [...prior, message]),
        (result) => {
          setBusy(false);
          if (result.error) setError(String(result.error));
          if (result.blocked) {
            setError(
              "Blocked by the project's monthly cap — the whole run, narration and cues. " +
                "Raise the cap, or turn effects off for this run.",
            );
          }
          setLog((prior) => [
            ...prior,
            `${result.succeeded} chunk(s) · ${result.effects_generated} cue(s) generated` +
              (result.effects_reused ? `, ${result.effects_reused} reused` : "") +
              ` · ${usd(Number(result.spent_micros ?? 0))}`,
          ]);
          onRefresh();
        },
      );
    } catch (e) {
      setBusy(false);
      setError(String(e));
    }
  }

  async function doExport() {
    setBusy(true);
    setError(null);
    try {
      setExported(await api.export(scriptId, chosen));
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title={script?.title ?? `Script ${scriptId}`}
        subtitle={
          <>
            {project?.name}
            {script ? ` · ${script.chunks} chunks · ${script.chars.toLocaleString()} chars` : ""}
          </>
        }
        actions={
          <>
            <button disabled={busy} onClick={() => generate(true)}>
              Dry run
            </button>
            <button className="primary" disabled={busy} onClick={() => generate(false)}>
              <SparkIcon /> {busy ? "Working…" : "Generate"}
            </button>
            <button
              disabled={busy || !timeline?.complete}
              onClick={doExport}
              title={
                timeline?.complete
                  ? "Stitch the cut and write the plan"
                  : "Every chunk needs a take before there is anything to stitch"
              }
            >
              <DownloadIcon /> Export
            </button>
          </>
        }
      />

      {error && (
        <Notice tone="error" title="That run did not finish">
          {error}
        </Notice>
      )}

      <Split
        main={
          <>
            <Card>
              {timeline && timeline.entries.length > 0 ? (
                <TrackView timeline={timeline} costMicros={cost?.cost_micros} />
              ) : (
                <Empty title="Nothing on the timeline yet">
                  Press Generate — it produces the narration and its effect cues in
                  one run, and the monthly cap is checked against both together.
                </Empty>
              )}
            </Card>

            <Card
              title="Chunks and takes"
              actions={
                <button
                  className="ghost small"
                  onClick={() => setDetail((v) => !v)}
                  aria-expanded={detail}
                >
                  {detail ? "hide the table" : "show the table"}
                </button>
              }
            >
              <ChunkList chunks={chunks} scriptId={scriptId} busy={busy} onChange={onRefresh} />
              {detail && (
                <div className="card-section">
                  <TimelineView timeline={timeline} />
                </div>
              )}
            </Card>
          </>
        }
        aside={
          <>
            <Card title="This run">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={withEffects}
                  disabled={busy}
                  onChange={(e) => setWithEffects(e.target.checked)}
                />
                Include effect cues
              </label>
              <Notice>
                One Generate covers narration and the accepted cues, and the
                monthly cap is checked against both together.{" "}
                <Link to={scriptPath(scriptId, "/effects")}>Manage cues →</Link>
              </Notice>

              {model?.continuity_mode === "none" && (
                <Notice tone="warn">
                  {model.label} has no cross-chunk continuity — neither request
                  stitching nor text conditioning — so seams between chunks are
                  unavoidable.
                </Notice>
              )}

              {formats.length > 0 && (
                <fieldset className="chip-set">
                  <legend>Export formats</legend>
                  <div className="chips">
                    {formats.map((format) => {
                      const on = chosen.includes(format.key);
                      return (
                        <button
                          key={format.key}
                          type="button"
                          // Was a <span onClick>, so it could not be reached or
                          // toggled by keyboard at all. `aria-pressed` is what
                          // makes a toggle announce its state.
                          className={`chip${on ? " on" : ""}`}
                          aria-pressed={on}
                          title={format.note}
                          onClick={() =>
                            setChosen((prior) =>
                              prior.includes(format.key)
                                ? prior.filter((k) => k !== format.key)
                                : [...prior, format.key],
                            )
                          }
                        >
                          {format.label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
              )}

              {log.length > 0 && (
                <div className="log card-section" role="status" aria-live="polite">
                  {log.join("\n")}
                </div>
              )}

              {exported && (
                <Notice tone="ok">
                  Exported {Object.keys(exported.masters).join(", ")} —{" "}
                  <Link to={scriptPath(scriptId, "/media")}>see the files →</Link>
                </Notice>
              )}
            </Card>

            <Card title="Cost">
              <CostPanel cost={cost} />
            </Card>
          </>
        }
      />
    </>
  );
}
