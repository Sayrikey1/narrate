import { useEffect, useRef, useState } from "react";
import { api, usd } from "../api";
import { ChunkList } from "../components/ChunkList";
import { RegeneratePanel } from "../components/RegeneratePanel";
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
  RegenerateBody,
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

  // Suspect and to-review alike: the aim is an episode with nothing to hear.
  const flaggedRows = chunks.filter((c) =>
    c.takes.some((t) => t.in_cut && (t.verify_status === "suspect" || t.verify_status === "review")),
  );
  const flagged = flaggedRows.map((c) => c.ordinal);
  const [fixing, setFixing] = useState(false);
  const fixButton = useRef<HTMLButtonElement | null>(null);
  // Nothing left to fix closes the panel, rather than leaving it to reopen by
  // itself the next time something is flagged.
  useEffect(() => {
    if (!flagged.length) setFixing(false);
  }, [flagged.length]);

  function closeFixing() {
    setFixing(false);
    requestAnimationFrame(() => fixButton.current?.focus());
  }

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

  /** Stream a background run into the log, then refresh the page's data. */
  function follow(runKey: string, summarise: (result: Record<string, unknown>) => string) {
    api.streamRun(
      runKey,
      (message) => setLog((prior) => [...prior, message]),
      (result) => {
        setBusy(false);
        if (result.error) setError(String(result.error));
        else setLog((prior) => [...prior, summarise(result)]);
        onRefresh();
      },
    );
  }

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      const started = await api.verify(scriptId);
      setLog([`Checking takes against the script (${started.mode}) — nothing is sent anywhere.`]);
      follow(started.run_key, (result) => {
        const rows = (result.results ?? []) as { status: string }[];
        const count = (status: string) => rows.filter((r) => r.status === status).length;
        const checked = count("suspect") + count("review") + count("clear");
        if (!rows.length) return "Nothing new to check.";
        return (
          `${checked} take(s) checked: ${count("suspect")} flagged, ` +
          `${count("review")} to review, ${count("clear")} with no issues found.` +
          (count("error") ? ` ${count("error")} could not be checked.` : "") +
          (count("unverified") ? ` ${count("unverified")} not checked.` : "")
        );
      });
    } catch (e) {
      setBusy(false);
      setError(String(e));
    }
  }

  async function regenerate(body: RegenerateBody) {
    setBusy(true);
    setError(null);
    try {
      // The rebuild writes the formats chosen on this page, so no master is
      // left behind still playing the old take.
      const started = await api.regenerate(
        scriptId,
        body.export ? { ...body, export_formats: chosen } : body,
      );
      if (!started.run_key) {
        setBusy(false);
        return;
      }
      setLog([`Regenerating chunk ${body.chunks.join(", ")} — up to ${started.worst_usd}.`]);
      follow(started.run_key, (result) => {
        const done = (result.regenerated ?? []) as {
          ordinal: number;
          statuses: string[];
          new_takes: number[];
          moved: boolean;
          stopped: string | null;
          reworded: boolean;
          words_restored: boolean;
          unknown_takes: number[];
          steady_takes: number[];
          cut_note: string | null;
        }[];
        const lines = done.map((r) => {
          if (!r.new_takes.length) {
            return (
              `chunk ${r.ordinal}: ${r.stopped || "no new take"}` +
              (r.unknown_takes?.length
                ? " — the request got no answer and may have been billed; reconcile before trying again"
                : "") +
              (r.words_restored ? " — no take was made with the new words, so the old ones were put back" : "")
            );
          }
          return (
            `chunk ${r.ordinal}: ${r.statuses.join(", ")}` +
            (r.moved
              ? ` — now in the cut${r.cut_note ? ` (${r.cut_note})` : ""}`
              : " — the cut is unchanged; listen and choose") +
            (r.reworded && !r.moved ? " (the take in the cut still has the old words)" : "") +
            (r.steady_takes?.length ? ` — ${r.steady_takes.length} made with the steadiest delivery` : "")
          );
        });
        const spent = usd(Number(result.spent_micros ?? 0));
        const rebuilt = result.exported as ExportResult | undefined;
        if (rebuilt) setExported(rebuilt);
        return [
          ...lines,
          result.mock ? `${spent} recorded (mock — nothing billed)` : `spent ${spent}`,
          ...(rebuilt ? ["The episode was rebuilt with the new takes."] : []),
          ...(result.export_error ? [`The episode was not rebuilt: ${String(result.export_error)}`] : []),
        ].join("\n");
      });
    } catch (e) {
      setBusy(false);
      setError(String(e));
      throw e;
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
                <>
                  <button
                    className="ghost small"
                    disabled={busy || !chunks.some((c) => c.takes.length)}
                    onClick={verify}
                    title="Transcribe the takes and compare them with the script. Free."
                  >
                    Check takes
                  </button>
                  <button
                    className="ghost small"
                    onClick={() => setDetail((v) => !v)}
                    aria-expanded={detail}
                  >
                    {detail ? "hide the table" : "show the table"}
                  </button>
                </>
              }
            >
              {flagged.length > 0 && (
                <Notice tone="warn" title={`${flagged.length} take(s) in the cut are flagged`}>
                  Words missing, added or changed in chunk {flagged.join(", ")}. Press ▶ beside
                  each finding to hear the spot, or fix them all at once.{" "}
                  <button
                    ref={fixButton}
                    className="small"
                    aria-expanded={fixing}
                    aria-disabled={busy}
                    onClick={() => {
                      if (!busy) setFixing((v) => !v);
                    }}
                  >
                    {fixing ? "Cancel" : "Fix all flagged…"}
                  </button>
                </Notice>
              )}
              {fixing && flaggedRows.length > 0 && (
                <RegeneratePanel
                  scriptId={scriptId}
                  chunks={flaggedRows}
                  busy={busy}
                  onRun={regenerate}
                  onClose={closeFixing}
                />
              )}
              <ChunkList
                chunks={chunks}
                scriptId={scriptId}
                busy={busy}
                onChange={onRefresh}
                onRegenerate={regenerate}
              />
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
