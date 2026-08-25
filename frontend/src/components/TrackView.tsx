import { useMemo, useRef, useState } from "react";
import { api, usd } from "../api";
import { formatClock } from "../time";
import * as transport from "../transport";
import type { Timeline, TimelineEntry } from "../types";
import { Player } from "./Player";
import { useTransport } from "./Transport";

/** The running order as two lanes rather than a table.
 *
 *  Effects sit in their own lane **above** the narration on purpose: it shows
 *  at a glance that they are overlays over the speech rather than inserts
 *  between it, which is the one thing about the export that surprises people.
 *
 *  Every number here already comes from the timeline endpoint — position is
 *  `start_s / runtime_s`, width is `duration_s / runtime_s`. This is
 *  presentation, not new data. */
export function TrackView({
  timeline,
  costMicros,
}: {
  timeline: Timeline;
  costMicros?: number;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  // One clock for the whole view: the transport's, so the playhead is the same
  // line in both lanes and agrees with the bar at the top of the page.
  const playback = useTransport();
  const rail = useRef<HTMLDivElement | null>(null);

  // A run with nothing generated has zero runtime; fall back to the planned
  // spans so the lanes are still readable rather than collapsing to nothing.
  const span = useMemo(() => {
    const ends = timeline.entries.map((e) => e.end_s);
    return Math.max(timeline.runtime_s, ...ends, 1);
  }, [timeline]);

  const narration = timeline.entries.filter((e) => e.kind === "narration");
  const overlays = timeline.entries.filter((e) => e.kind !== "narration");
  const ticks = useMemo(() => tickPositions(span), [span]);
  const headPct = span ? (playback.position / span) * 100 : 0;

  /** Clicking anywhere on a lane seeks the whole timeline there. */
  function seekFrom(event: React.MouseEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    transport.seek(ratio * span);
  }

  return (
    <div className="track">
      <div className="track-head">
        <span className="title">{timeline.title}</span>
        <span className="meta">
          {formatClock(span)}
          {costMicros !== undefined ? ` · ${usd(costMicros)}` : ""}
        </span>
      </div>

      <div className="ruler">
        {ticks.map((seconds) => (
          <span
            key={seconds}
            className="ruler-tick"
            style={{ left: `${(seconds / span) * 100}%` }}
          >
            {formatClock(seconds)}
          </span>
        ))}
      </div>

      <div className="lane">
        <div className="lane-label">Effects — overlay track</div>
        <div className="lane-rail short" onClick={seekFrom}>
          {overlays.map((entry) => (
            <Block
              key={entry.index}
              entry={entry}
              span={span}
              selected={selected === entry.index}
              onSelect={setSelected}
            />
          ))}
          {!overlays.length && (
            <span className="ruler-tick" style={{ left: 8, top: 8 }}>
              no effects
            </span>
          )}
          {playback.playing && <div className="playhead" style={{ left: `${headPct}%` }} />}
        </div>
      </div>

      <div className="lane">
        <div className="lane-label">Narration</div>
        <div className="lane-rail" ref={rail} onClick={seekFrom}>
          {narration.map((entry) => (
            <Block
              key={entry.index}
              entry={entry}
              span={span}
              selected={selected === entry.index}
              onSelect={setSelected}
            />
          ))}
          {playback.playing && <div className="playhead" style={{ left: `${headPct}%` }} />}
        </div>
      </div>

      <div className="track-legend">
        <span>
          <i className="swatch" style={{ background: "var(--narration)" }} /> narration
        </span>
        <span>
          <i className="swatch" style={{ background: "var(--effect)" }} /> effect
        </span>
        <span>
          <i
            className="swatch"
            style={{ border: "1px dashed var(--planned)", background: "transparent" }}
          />{" "}
          planned, not generated
        </span>
        {!timeline.complete && <span className="warn">timings provisional</span>}
        <span className="faint">click a lane to seek · space to play</span>
      </div>

      {selected !== null && <Detail timeline={timeline} index={selected} />}
    </div>
  );
}

function Block({
  entry,
  span,
  selected,
  onSelect,
}: {
  entry: TimelineEntry;
  span: number;
  selected: boolean;
  onSelect: (index: number) => void;
}) {
  const kind = entry.kind === "narration" ? "narration" : entry.generated ? "effect" : "planned";
  const left = (entry.start_s / span) * 100;
  // A very short cue would otherwise be invisible; floor it at something
  // clickable rather than letting it vanish.
  const width = Math.max((entry.duration_s / span) * 100, 0.6);
  // Below roughly this width the label is a couple of letters and reads as
  // noise, so the block becomes a marker and the tooltip carries the name.
  const roomForLabel = width > 7;

  return (
    <div
      className={`block ${kind}${selected ? " selected" : ""}`}
      style={{ left: `${left}%`, width: `${width}%` }}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(entry.index);
        transport.seek(entry.start_s);
      }}
      title={`${entry.start} – ${entry.end}  ${entry.label}`}
    >
      {entry.kind === "narration" && roomForLabel && (
        <span className="block-num">{entry.chunk_ordinal}</span>
      )}
      {roomForLabel ? entry.label : null}
    </div>
  );
}

function Detail({ timeline, index }: { timeline: Timeline; index: number }) {
  const entry = timeline.entries.find((e) => e.index === index);
  if (!entry) return null;
  const src = srcFor(entry);

  return (
    <div className="subpanel track-panel">
      <div className="row track-meta">
        <span className={`tag ${entry.kind === "narration" ? "" : entry.kind}`}>
          {entry.kind}
        </span>
        <span className="mono">
          {entry.start} – {entry.end}
        </span>
        <span className="mono faint">{entry.duration_s.toFixed(2)}s</span>
        {entry.drift_s ? (
          <span className="warn mono">
            {entry.drift_s > 0 ? "+" : ""}
            {entry.drift_s.toFixed(1)}s vs target
          </span>
        ) : null}
      </div>
      <div>{entry.label}</div>
      {src ? (
        <Player src={src} duration={entry.duration_s} wide />
      ) : (
        <span className="note warn">Not generated yet — nothing to play.</span>
      )}
    </div>
  );
}

function srcFor(entry: TimelineEntry): string | null {
  if (entry.kind === "narration" && entry.take_id) return api.audioTake(entry.take_id);
  if (entry.effect_id) return api.audioEffect(entry.effect_id);
  return null;
}

/** Roughly six labelled ticks, on a round interval. */
function tickPositions(span: number): number[] {
  const candidates = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = candidates.find((c) => span / c <= 7) ?? 900;
  const out: number[] = [];
  for (let t = 0; t <= span; t += step) out.push(t);
  return out;
}
