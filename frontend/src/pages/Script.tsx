import { useEffect, useRef, useState } from "react";
import { api, usd } from "../api";
import { ChunkList } from "../components/ChunkList";
import { CostPanel } from "../components/CostPanel";
import { DeletePanel } from "../components/DeletePanel";
import { ExportCard } from "../components/ExportCard";
import { GeneratePanel } from "../components/GeneratePanel";
import { CloseIcon, SparkleIcon } from "../components/Icon";
import { RegeneratePanel } from "../components/RegeneratePanel";
import { ReplacePanel } from "../components/ReplacePanel";
import { History } from "../components/History";
import { Stepper } from "../components/Stepper";
import { TimelineView } from "../components/Timeline";
import { TrackView } from "../components/TrackView";
import { navigate } from "../router";
import { Empty, Notice } from "../ui/feedback";
import { PageHeader } from "../ui/layout";
import type {
  AudioFormatInfo,
  ChunkRow,
  Cost,
  Credits,
  EpisodeStatus,
  ExportResult,
  Model,
  Project,
  Publish,
  RegenerateBody,
  ScriptSummary,
  Slot,
  Timeline,
} from "../types";

/** The workbench: generate, listen, check, fix, choose takes, export. */
export function ScriptPage({
  scriptId,
  script,
  project,
  model,
  voiceName,
  timeline,
  chunks,
  slots,
  cost,
  credits,
  onRefresh,
}: {
  scriptId: number;
  script: ScriptSummary | null;
  project: Project | null;
  model: Model | undefined;
  voiceName: string | null;
  timeline: Timeline | null;
  chunks: ChunkRow[];
  slots: Slot[];
  cost: Cost | null;
  credits: Credits | null;
  onRefresh: () => void;
}) {
  const [formats, setFormats] = useState<AudioFormatInfo[]>([]);
  const [chosen, setChosen] = useState<string[]>(["wav", "mp3"]);
  const [mix, setMix] = useState(true);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  // Which panel the log belongs to: the generate panel shows its own run;
  // a check or a regeneration gets a card of its own.
  const [logFrom, setLogFrom] = useState<"generate" | "other" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exported, setExported] = useState<ExportResult | null>(null);
  const [publish, setPublish] = useState<Publish | null>(null);
  // `#generate` and `#fix` open those panels on arrival, which is where the
  // Projects page sends "Generate what's missing" and "Fix what's flagged".
  const [generating, setGenerating] = useState(() => window.location.hash === "#generate");
  const [episodePanel, setEpisodePanel] = useState<"none" | "replace" | "delete">("none");
  // Cancelling a panel hands focus back to the button that opened it.
  const replaceTrigger = useRef<HTMLButtonElement | null>(null);
  const deleteTrigger = useRef<HTMLButtonElement | null>(null);
  const [timing, setTiming] = useState(false);

  // Suspect and to-review alike: the aim is an episode with nothing to hear —
  // less what a person has listened to and said is fine, which regenerating
  // would only pay to have flagged again.
  const flaggedRows = chunks.filter((c) =>
    c.takes.some(
      (t) =>
        t.in_cut &&
        !t.verify_accepted &&
        (t.verify_status === "suspect" || t.verify_status === "review"),
    ),
  );
  const flagged = flaggedRows.map((c) => c.ordinal);
  const cutOf = (c: ChunkRow) => c.takes.find((t) => t.in_cut);
  const suspects = flaggedRows.filter((c) => cutOf(c)?.verify_status === "suspect").length;
  const toReview = flaggedRows.length - suspects;
  const [fixing, setFixing] = useState(false);
  const fixButton = useRef<HTMLButtonElement | null>(null);
  const askedToFix = useRef(window.location.hash === "#fix");
  // Nothing left to fix closes the panel, rather than leaving it to reopen by
  // itself the next time something is flagged. Asked for by the link, it opens
  // once the chunks are in — and only if there is something to fix by then.
  useEffect(() => {
    if (askedToFix.current && chunks.length) {
      askedToFix.current = false;
      if (flagged.length) setFixing(true);
    }
    if (!flagged.length) setFixing(false);
  }, [flagged.length, chunks.length]);

  const missing = (timeline?.entries ?? [])
    .filter((e) => e.kind === "narration" && !e.generated)
    .map((e) => e.chunk_ordinal ?? 0);

  function closeFixing() {
    setFixing(false);
    requestAnimationFrame(() => fixButton.current?.focus());
  }

  function openGenerate() {
    setGenerating(true);
    requestAnimationFrame(() =>
      document.getElementById("generate")?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  useEffect(() => {
    if (generating) openGenerate();
    // Only on arrival: the button scrolls for itself afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    api
      .formats()
      .then(setFormats)
      .catch(() => undefined);
  }, []);

  // Whether the last export is still current, for the stepper: asked again
  // whenever the timeline moves or an export lands.
  const [status, setStatus] = useState<EpisodeStatus | null>(null);
  useEffect(() => {
    api
      .status(scriptId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [scriptId, timeline, exported]);

  // The publish pack's state feeds the last step. Reloaded with the timeline,
  // which changes whenever anything this page does lands.
  useEffect(() => {
    api
      .publish(scriptId)
      .then(setPublish)
      .catch(() => setPublish(null));
  }, [scriptId, timeline]);

  async function generate(withEffects: boolean, maxUsd: number) {
    setBusy(true);
    setError(null);
    setLog([]);
    setLogFrom("generate");
    try {
      const started = await api.generate(scriptId, {
        dry_run: false,
        with_effects: withEffects,
        max_spend_usd: maxUsd,
      });
      const p = started.projection;
      setLog([
        `narration ${p.chunks} chunk(s), ${p.chars.toLocaleString()} chars — ${usd(p.speech_micros)}`,
        ...(p.effects
          ? [`effects   ${p.effects} cue(s), ${p.effect_seconds}s — ${usd(p.effect_micros)}`]
          : []),
        `at most ${usd(p.total_micros)}`,
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
              "Blocked by the project's monthly cap — the whole run, narration and effects. " +
                "Raise the cap, or leave the effects out of this run.",
            );
          }
          setLog((prior) => [
            ...prior,
            `${result.succeeded} chunk(s) · ${result.effects_generated} effect(s) generated` +
              (result.effects_reused ? `, ${result.effects_reused} reused` : "") +
              ` · spent ${usd(Number(result.spent_micros ?? 0))}`,
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
    setLogFrom("other");
    try {
      const started = await api.verify(scriptId);
      setLog([`Checking takes against the script (${started.mode}) — nothing is sent anywhere.`]);
      follow(started.run_key, (result) => {
        const rows = (result.results ?? []) as { status: string; accepted?: boolean }[];
        const count = (status: string) =>
          rows.filter((r) => r.status === status && !r.accepted).length;
        const byEar = rows.filter((r) => r.accepted).length;
        const checked = count("suspect") + count("review") + count("clear") + byEar;
        if (!rows.length) return "Nothing new to check.";
        return (
          `${checked} take(s) checked: ${count("suspect")} flagged, ` +
          `${count("review")} to review, ${count("clear")} with no issues found.` +
          (byEar ? ` ${byEar} flagged but already fine by ear.` : "") +
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
    setLogFrom("other");
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
          joined_takes: number[];
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
            (r.steady_takes?.length ? ` — ${r.steady_takes.length} made with the steadiest delivery` : "") +
            (r.joined_takes?.length ? " — a paragraph break joined where the problem sat" : "")
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
      setExported(await api.export(scriptId, chosen, undefined, undefined, mix));
      onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const title = script?.title ?? `Script ${scriptId}`;

  return (
    <>
      <PageHeader
        icon="🎙️"
        title={title}
        subtitle={
          <>
            {project?.name}
            {script && (
              <>
                {" · "}
                {script.chunks} chunk{script.chunks === 1 ? "" : "s"} ·{" "}
                <span className="mono">{script.chars.toLocaleString()}</span> chars
              </>
            )}
            {voiceName && ` · 🗣️ ${voiceName}`}
            {model && ` · ${model.label}`}
          </>
        }
        actions={
          <>
            <button
              type="button"
              className="large"
              disabled={busy || !chunks.some((c) => c.takes.length)}
              onClick={verify}
              title="Transcribe the takes and compare them with the script. Nothing is sent anywhere."
            >
              🔍 Check takes <span className="btn-note">free</span>
            </button>
            <button
              type="button"
              className="primary large"
              disabled={busy}
              aria-expanded={generating}
              onClick={() => (generating ? setGenerating(false) : openGenerate())}
            >
              <SparkleIcon />
              {busy && logFrom === "generate" ? "Generating…" : "Generate…"}
            </button>
            <button
              ref={replaceTrigger}
              type="button"
              className="ghost large"
              disabled={busy}
              aria-expanded={episodePanel === "replace"}
              onClick={() => setEpisodePanel((v) => (v === "replace" ? "none" : "replace"))}
            >
              Replace script…
            </button>
            <button
              ref={deleteTrigger}
              type="button"
              className="ghost danger large"
              disabled={busy}
              aria-expanded={episodePanel === "delete"}
              onClick={() => setEpisodePanel((v) => (v === "delete" ? "none" : "delete"))}
            >
              Delete…
            </button>
          </>
        }
      />

      {episodePanel === "replace" && (
        <ReplacePanel
          scriptId={scriptId}
          busy={busy}
          onClose={() => {
            setEpisodePanel("none");
            replaceTrigger.current?.focus();
          }}
          onDone={(plan) => {
            setEpisodePanel("none");
            setLogFrom("other");
            setLog([
              `Script replaced: ${plan.kept} chunk(s) kept with their takes, ${plan.new} new` +
                (plan.retired ? `, ${plan.retired} dropped` : "") +
                `. Generate what changed — about ${plan.quote_usd}.`,
            ]);
            onRefresh();
          }}
        />
      )}
      {episodePanel === "delete" && (
        <DeletePanel
          what="episode"
          run={(body) => api.deleteScript(scriptId, body)}
          onClose={() => {
            setEpisodePanel("none");
            deleteTrigger.current?.focus();
          }}
          onDone={() => {
            navigate("/", true);
            onRefresh();
          }}
        />
      )}

      <Stepper chunks={chunks} timeline={timeline} exported={exported} publish={publish} status={status} />

      {error && (
        <Notice tone="error" title="That run did not finish">
          {error}
        </Notice>
      )}

      <div className="bench">
        <div className="bench-main">
          {generating && (
            <GeneratePanel
              scriptId={scriptId}
              slots={slots}
              missing={missing}
              cost={cost}
              credits={credits}
              busy={busy}
              running={busy && logFrom === "generate"}
              log={logFrom === "generate" ? log : []}
              onGenerate={generate}
              onClose={() => {
                setGenerating(false);
                if (!busy && logFrom === "generate") {
                  setLog([]);
                  setLogFrom(null);
                }
              }}
            />
          )}

          {logFrom === "other" && log.length > 0 && (
            <section className="panel panel-stack" aria-labelledby="run-h">
              <div className="card-head">
                <h2 className="card-title" id="run-h">
                  🧾 Latest run
                </h2>
                <button
                  type="button"
                  className="round"
                  aria-label="Close the run log"
                  disabled={busy}
                  onClick={() => {
                    setLog([]);
                    setLogFrom(null);
                  }}
                >
                  <CloseIcon />
                </button>
              </div>
              <div className="log" role="status" aria-live="polite">
                {log.join("\n")}
              </div>
            </section>
          )}

          <section className="panel" aria-label="Track">
            {timeline && timeline.entries.length > 0 ? (
              <TrackView timeline={timeline} chunks={chunks} costMicros={cost?.cost_micros} />
            ) : (
              <Empty title="Nothing on the timeline yet">
                Press Generate — it produces the narration and its effects in one run, and the
                monthly cap is checked against both together.
              </Empty>
            )}
          </section>

          <section className="panel panel-stack" aria-labelledby="chunks-h">
            <div className="card-head">
              <h2 className="card-title" id="chunks-h">
                🧩 Chunks &amp; takes
              </h2>
              <div className="toolbar">
                <button
                  type="button"
                  className="small"
                  disabled={busy || !chunks.some((c) => c.takes.length)}
                  onClick={verify}
                  title="Transcribe the takes and compare them with the script. Free."
                >
                  🔍 Check new takes · free
                </button>
                <button
                  type="button"
                  className="small"
                  onClick={() => setTiming((v) => !v)}
                  aria-expanded={timing}
                >
                  {timing ? "Hide timing table" : "Show timing table"}
                </button>
              </div>
            </div>

            {flagged.length > 0 && (
              <div className="notice error banner">
                <span className="banner-icon" aria-hidden>
                  🚩
                </span>
                <div className="banner-body">
                  <strong>
                    {suspects > 0
                      ? `${suspects} take${suspects === 1 ? "" : "s"} in the cut ${suspects === 1 ? "is" : "are"} flagged`
                      : ""}
                    {suspects > 0 && toReview > 0 ? ", and " : ""}
                    {toReview > 0
                      ? `${toReview}${suspects > 0 ? "" : ` take${toReview === 1 ? "" : "s"} in the cut`} ${toReview === 1 ? "is" : "are"} worth a listen`
                      : ""}
                  </strong>
                  <span className="dim">
                    Words missing, added or changed in chunk{flagged.length === 1 ? "" : "s"}{" "}
                    {flagged.join(", ")}. Press ▶ beside a finding to hear the exact spot — or fix
                    them all at once.
                  </span>
                </div>
                <div className="banner-actions">
                  <button
                    ref={fixButton}
                    type="button"
                    aria-expanded={fixing}
                    aria-disabled={busy}
                    onClick={() => {
                      if (!busy) setFixing((v) => !v);
                    }}
                  >
                    {fixing ? "Cancel" : "Fix all flagged…"}
                  </button>
                </div>
              </div>
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
              timeline={timeline}
              busy={busy}
              onChange={onRefresh}
              onRegenerate={regenerate}
              onGenerateMissing={openGenerate}
            />
            {timing && <TimelineView timeline={timeline} />}
          </section>
        </div>

        <div className="bench-side">
          <ExportCard
            scriptId={scriptId}
            timeline={timeline}
            chunks={chunks}
            formats={formats}
            chosen={chosen}
            onChoose={setChosen}
            mix={mix}
            onMix={setMix}
            busy={busy}
            exported={exported}
            onExport={doExport}
            onGenerate={openGenerate}
          />

          <section className="panel panel-stack" aria-labelledby="cost-h">
            <h2 className="card-title" id="cost-h">
              💰 What {title} has cost
            </h2>
            <CostPanel cost={cost} projectName={project?.name} />
          </section>

          <History scriptId={scriptId} refreshKey={status} />
        </div>
      </div>
    </>
  );
}
