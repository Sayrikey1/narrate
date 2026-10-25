import { Link, scriptPath } from "../router";
import { Notice } from "../ui/feedback";
import type { AudioFormatInfo, ChunkRow, ExportResult, Timeline } from "../types";

/** Export: what stands in the way, in words, and how to clear it.
 *
 *  The button used to be disabled with the reason in a tooltip — which most
 *  browsers do not show on a disabled button, so it looked broken. Now the
 *  card lists what blocks it, offers the fix, and says under the button what
 *  will unlock it. */
export function ExportCard({
  scriptId,
  timeline,
  chunks,
  formats,
  chosen,
  onChoose,
  mix,
  onMix,
  busy,
  exported,
  onExport,
  onGenerate,
}: {
  scriptId: number;
  timeline: Timeline | null;
  chunks: ChunkRow[];
  formats: AudioFormatInfo[];
  chosen: string[];
  onChoose: (next: string[]) => void;
  mix: boolean;
  onMix: (on: boolean) => void;
  busy: boolean;
  exported: ExportResult | null;
  onExport: () => void;
  /** Open the generate panel on what is missing. */
  onGenerate: () => void;
}) {
  const narration = (timeline?.entries ?? []).filter((e) => e.kind === "narration");
  const missing = narration.filter((e) => !e.generated).map((e) => e.chunk_ordinal);
  const total = chunks.length;
  const complete = total > 0 && missing.length === 0;
  const hasEffects = (timeline?.entries ?? []).some((e) => e.kind === "effect" && e.generated);
  const flagged = chunks.filter((c) =>
    c.takes.some(
      (t) =>
        t.in_cut &&
        !t.verify_accepted &&
        (t.verify_status === "suspect" || t.verify_status === "review"),
    ),
  ).length;
  const nothingChosen = chosen.length === 0;
  const many = missing.length > 1;

  return (
    <section className="panel panel-stack" aria-labelledby="export-h">
      <div className="card-head">
        <h2 className="card-title" id="export-h">
          📦 Export
        </h2>
        {exported ? (
          <span className="tag ok">✓ Exported</span>
        ) : complete ? (
          <span className="tag ok">Ready</span>
        ) : (
          <span className="tag warn">⏸ Blocked</span>
        )}
      </div>

      <ul className="checklist">
        <li>
          <span className="mark ok" aria-hidden>
            ✓
          </span>
          <span>
            {complete
              ? `All ${total} chunks have a take in the cut`
              : `${total - missing.length} of ${total} chunks have a take in the cut`}
          </span>
        </li>
        {!complete && total > 0 && (
          <li>
            <span className="mark bad" aria-hidden>
              ✕
            </span>
            <span>
              <strong>
                Chunk{many ? "s" : ""} {missing.join(", ")} {many ? "have" : "has"} no audio.
              </strong>{" "}
              <button type="button" className="link-button" onClick={onGenerate}>
                Generate {many ? "them" : "it"}
              </button>{" "}
              and export unlocks.
            </span>
          </li>
        )}
        {flagged > 0 && (
          <li>
            <span className="mark warn" aria-hidden>
              !
            </span>
            <span className="dim">
              {flagged} flagged take{flagged === 1 ? "" : "s"} would export as{" "}
              {flagged === 1 ? "it is" : "they are"}. Allowed, but worth fixing first.
            </span>
          </li>
        )}
      </ul>

      {formats.length > 0 && (
        <fieldset className="chip-set">
          <legend>Formats</legend>
          <div className="chips">
            {formats.map((format) => {
              const on = chosen.includes(format.key);
              return (
                <button
                  key={format.key}
                  type="button"
                  // A toggle announces its state with `aria-pressed`; a span
                  // with a click handler could not be reached by keyboard.
                  className={`chip${on ? " on" : ""}`}
                  aria-pressed={on}
                  title={format.note}
                  onClick={() =>
                    onChoose(on ? chosen.filter((k) => k !== format.key) : [...chosen, format.key])
                  }
                >
                  {on ? "✓ " : ""}
                  {format.label}
                </button>
              );
            })}
          </div>
        </fieldset>
      )}

      {hasEffects && (
        <label className="checkbox">
          <input type="checkbox" checked={mix} disabled={busy} onChange={(e) => onMix(e.target.checked)} />
          <span>
            ✨ Also mix the effects into an <code>_fx</code> master
            <span className="checkbox-hint">
              The plain master is kept for editors who want effects on their own track.
            </span>
          </span>
        </label>
      )}

      <button
        type="button"
        className={complete && !nothingChosen ? "primary export-go" : "export-go"}
        disabled={busy || !complete || nothingChosen}
        onClick={onExport}
        aria-describedby={!complete || nothingChosen ? "export-why" : undefined}
      >
        ⤓ Export {chosen.length} format{chosen.length === 1 ? "" : "s"}
      </button>
      {(!complete || nothingChosen) && (
        <p id="export-why" className="export-why">
          {!complete
            ? `Unlocks as soon as chunk${many ? "s" : ""} ${missing.join(", ")} ${many ? "have" : "has"} audio.`
            : "Choose at least one format."}
        </p>
      )}

      {exported && (
        <Notice tone="ok">
          Exported {Object.keys(exported.masters).join(", ")}
          {exported.effects_mixed > 0 &&
            `, each also with its ${exported.effects_mixed} effect${exported.effects_mixed === 1 ? "" : "s"} mixed in (_fx)`}{" "}
          — <Link to={scriptPath(scriptId, "/media")}>see the files →</Link>
        </Notice>
      )}
    </section>
  );
}
