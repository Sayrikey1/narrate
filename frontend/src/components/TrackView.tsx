import { Fragment, useMemo, useState } from "react";
import { api, usd, peaksUrl } from "../api";
import { spoken } from "../findings";
import { formatClock } from "../time";
import * as transport from "../transport";
import type { ChunkRow, Timeline, TimelineEntry } from "../types";
import { Player } from "./Player";
import { usePeaks } from "./Wave";
import { useTransport } from "./Transport";

/** The running order as two lanes over one ruler.
 *
 *  Effects sit in their own lane **above** the narration on purpose: it shows
 *  at a glance that they are overlays on the speech rather than inserts
 *  between it, which is the one thing about the export that surprises people.
 *
 *  Every number here already comes from the timeline endpoint — position is
 *  `start_s / runtime_s`, width is `duration_s / runtime_s`. This is
 *  presentation, not new data. */
export function TrackView({
  timeline,
  chunks,
  costMicros,
}: {
  timeline: Timeline;
  chunks: ChunkRow[];
  costMicros?: number;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  // One clock for the whole view: the transport's, so the playhead is the same
  // line in both lanes and agrees with the player at the bottom of the page.
  const playback = useTransport();

  // A run with nothing generated has zero runtime; fall back to the planned
  // spans so the lanes are still readable rather than collapsing to nothing.
  const span = useMemo(() => {
    const ends = timeline.entries.map((e) => e.end_s);
    return Math.max(timeline.runtime_s, ...ends, 1);
  }, [timeline]);

  const narration = timeline.entries.filter((e) => e.kind === "narration");
  const overlays = timeline.entries.filter((e) => e.kind !== "narration");
  const ticks = useMemo(() => tickPositions(span), [span]);
  const headPct = (playback.position / span) * 100;
  const firstMissing = narration.find((e) => !e.generated);
  const byOrdinal = new Map(chunks.map((c) => [c.ordinal, c]));

  /** Clicking anywhere on a lane seeks the whole timeline there. */
  function seekFrom(event: React.MouseEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    transport.seek(ratio * span);
  }

  function pick(entry: TimelineEntry) {
    setSelected(entry.index);
    transport.seek(entry.start_s);
  }

  return (
    <div className="track">
      <div className="track-head">
        <h2 className="card-title">〰️ Track</h2>
        <span className="meta">
          <span className="mono">{formatClock(timeline.runtime_s)}</span> runtime
          {costMicros !== undefined && (
            <>
              {" · "}
              <span className="mono">{usd(costMicros)}</span> spent
            </>
          )}
          {" · click a block to select it"}
        </span>
      </div>

      <div className="track-body">
        <div className="lane-labels" aria-hidden>
          <div className="fx">
            <strong>✨ Effects</strong>
            <span>overlay — adds no time</span>
          </div>
          <div className="narr">
            <strong>🎙️ Narration</strong>
            <span>
              {narration.length} chunk{narration.length === 1 ? "" : "s"}
            </span>
          </div>
        </div>

        <div className="track-lanes">
          <div className="ruler" aria-hidden>
            {ticks.map((seconds) => (
              <span key={seconds} className="ruler-tick" style={{ left: `${(seconds / span) * 100}%` }}>
                {formatClock(seconds)}
              </span>
            ))}
          </div>

          <div className="lane-rail fx" onClick={seekFrom}>
            {overlays.map((entry) => {
              const left = (entry.start_s / span) * 100;
              const width = Math.max((entry.duration_s / span) * 100, 0.5);
              const planned = !entry.generated;
              return (
                <Fragment key={entry.index}>
                  <span
                    className={planned ? "pin-bar planned" : "pin-bar"}
                    style={{ left: `${left}%`, width: `${width}%` }}
                    aria-hidden
                  />
                  <button
                    type="button"
                    className={["pin", planned ? "planned" : "", selected === entry.index ? "selected" : ""]
                      .filter(Boolean)
                      .join(" ")}
                    style={{ left: `${left}%` }}
                    aria-label={`${planned ? "Planned effect, not generated yet" : "Effect"}: ${entry.label}, at ${entry.start}`}
                    title={`${entry.start} – ${entry.end}  ${entry.label}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      pick(entry);
                    }}
                  >
                    <span aria-hidden>✨</span>
                  </button>
                </Fragment>
              );
            })}
            {!overlays.length && <span className="lane-empty">no effects</span>}
          </div>

          <div className="lane-rail narr" onClick={seekFrom}>
            {narration.map((entry) => {
              const left = (entry.start_s / span) * 100;
              const width = Math.max((entry.duration_s / span) * 100, 0.6);
              const status = statusOf(byOrdinal.get(entry.chunk_ordinal ?? -1), entry);
              // Below roughly this width the opening words are a couple of
              // letters and read as noise; the number and the tooltip carry it.
              const label = width >= 7 ? `${status.icon}${entry.chunk_ordinal} · ${spoken(entry.label)}` : `${status.icon}${entry.chunk_ordinal}`;
              const played =
                entry.duration_s > 0
                  ? Math.min(100, Math.max(0, ((playback.position - entry.start_s) / entry.duration_s) * 100))
                  : 0;
              return (
                <button
                  key={entry.index}
                  type="button"
                  className={["block", entry.generated ? "" : "failed", selected === entry.index ? "selected" : ""]
                    .filter(Boolean)
                    .join(" ")}
                  style={{ left: `calc(${left}% + 2px)`, width: `calc(${width}% - 4px)` }}
                  aria-pressed={selected === entry.index}
                  aria-label={`Chunk ${entry.chunk_ordinal}, ${entry.start} to ${entry.end}${entry.generated ? "" : ", no audio yet"}`}
                  title={`${entry.start} – ${entry.end}  ${entry.label}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    pick(entry);
                  }}
                >
                  {entry.generated && (
                    <span
                      className="block-fill"
                      aria-hidden
                      style={{
                        background: `linear-gradient(90deg, var(--narr2) ${played}%, var(--narr) ${played}%)`,
                      }}
                    />
                  )}
                  <span className="block-label">{label}</span>
                </button>
              );
            })}
          </div>

          {(playback.playing || playback.position > 0) && (
            <div className="playhead" style={{ left: `${Math.min(100, headPct)}%` }} aria-hidden />
          )}
        </div>
      </div>

      <div className="track-legend">
        <span>
          <i className="swatch narr" /> narration
        </span>
        <span>
          <i className="swatch fx" /> effect
        </span>
        <span>
          <i className="swatch planned" /> planned, not generated
        </span>
        <span>
          <i className="swatch failed" /> no audio
        </span>
        {firstMissing && (
          <span className="warn">⏳ Timings from {formatClock(firstMissing.start_s)} on are provisional</span>
        )}
      </div>

      {selected !== null && (
        <Detail timeline={timeline} index={selected} chunk={chunkFor(timeline, selected, byOrdinal)} />
      )}
    </div>
  );
}

function chunkFor(timeline: Timeline, index: number, byOrdinal: Map<number, ChunkRow>) {
  const entry = timeline.entries.find((e) => e.index === index);
  return entry?.chunk_ordinal != null ? byOrdinal.get(entry.chunk_ordinal) : undefined;
}

/** How a chunk's take in the cut stands, as an icon and a sentence. */
function statusOf(
  chunk: ChunkRow | undefined,
  entry: TimelineEntry,
): { icon: string; text: string; tone: string } {
  if (!entry.generated) return { icon: "⛔ ", text: "⛔ No audio yet", tone: "error" };
  const take = chunk?.takes.find((t) => t.in_cut);
  if (!take) return { icon: "", text: "", tone: "" };
  const problems = take.findings.filter((f) => f.severity !== "info").length;
  const counted = `${problems} finding${problems === 1 ? "" : "s"}`;
  const flaggedBy = take.verify_status === "suspect" || take.verify_status === "review";
  if (take.verify_accepted && flaggedBy)
    return { icon: "", text: `✓ Take ${take.ordinal} is in the cut · fine by ear`, tone: "ok" };
  if (take.verify_status === "suspect")
    return { icon: "🚩 ", text: `🚩 Take ${take.ordinal} is in the cut · ${counted}`, tone: "error" };
  if (take.verify_status === "review")
    return { icon: "👀 ", text: `👀 Take ${take.ordinal} is in the cut · worth a listen`, tone: "warn" };
  if (take.verify_status === "clear")
    return { icon: "", text: `✓ Take ${take.ordinal} is in the cut · no issues found`, tone: "ok" };
  return { icon: "", text: `Take ${take.ordinal} is in the cut · not checked yet`, tone: "dim" };
}

function Detail({ timeline, index, chunk }: { timeline: Timeline; index: number; chunk?: ChunkRow }) {
  const entry = timeline.entries.find((e) => e.index === index);
  // Before the early return: a hook runs on every render or not at all.
  const peaks = usePeaks(
    entry?.kind === "narration" && entry.take_id
      ? peaksUrl.take(entry.take_id, 96)
      : entry?.effect_id
        ? peaksUrl.effect(entry.effect_id, 48)
        : null,
  );
  if (!entry) return null;
  const src =
    entry.kind === "narration" && entry.take_id
      ? api.audioTake(entry.take_id)
      : entry.effect_id
        ? api.audioEffect(entry.effect_id)
        : null;
  const status = entry.kind === "narration" ? statusOf(chunk, entry) : null;
  const what =
    entry.kind === "narration"
      ? `🎙️ Narration · chunk ${entry.chunk_ordinal}`
      : entry.generated
        ? "✨ Effect"
        : "✨ Planned effect";

  return (
    <div className="track-panel">
      <div className="track-meta">
        <span className="tag">{what}</span>
        <span className="mono">
          {entry.start} – {entry.end}
        </span>
        <span className="mono">· {entry.duration_s.toFixed(1)}s</span>
        {entry.drift_s ? (
          <span className={Math.abs(entry.drift_s) > 10 ? "warn" : "dim"}>
            🎯 aimed at {formatClock(entry.target_s ?? 0)}, landed {entry.drift_s > 0 ? "+" : ""}
            {entry.drift_s.toFixed(1)}s
          </span>
        ) : null}
      </div>
      <p className="track-quote">“{spoken(chunk?.text ?? entry.label)}”</p>
      {status?.text && <span className={status.tone}>{status.text}</span>}
      {src ? (
        <Player src={src} duration={entry.duration_s} wide peaks={peaks} />
      ) : (
        <span className="faint">Not generated yet — nothing to play.</span>
      )}
    </div>
  );
}

/** Roughly six labelled ticks, on a round interval. */
function tickPositions(span: number): number[] {
  const candidates = [1, 2, 5, 10, 15, 30, 60, 120, 240, 300, 600];
  const step = candidates.find((c) => span / c <= 6) ?? 900;
  const out: number[] = [];
  for (let t = 0; t <= span; t += step) out.push(t);
  return out;
}
