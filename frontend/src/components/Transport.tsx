import { useEffect, useState } from "react";
import { formatClock } from "../time";
import * as transport from "../transport";
import type { TransportState } from "../transport";
import { NextIcon, PauseIcon, PlayIcon, PrevIcon } from "./Icon";

/** An effect on the scrub bar: where it starts, and whether it has audio yet. */
export interface TransportMarker {
  at: number;
  planned: boolean;
}

/** The player along the bottom of every page.
 *
 *  Lives in the shell rather than on a page, so playback survives navigation —
 *  looking at the cost page should not stop the episode. */
export function Transport({
  title,
  subtitle,
  chunkStarts,
  markers,
}: {
  title?: string;
  /** "HINDSIGHT · Rachel". */
  subtitle?: string;
  /** Where each narration chunk starts, for previous and next. */
  chunkStarts: number[];
  markers: TransportMarker[];
}) {
  const state = useTransport();

  // Space plays and pauses, unless something is being typed into.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName)) return;
      if (target?.isContentEditable) return;
      event.preventDefault();
      transport.toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!state.ready) return null;

  const pct = state.duration ? (state.position / state.duration) * 100 : 0;
  const now = formatClock(state.position);
  const total = formatClock(state.duration);

  // Back to the start of this chunk; from its first moments, the one before.
  function previous() {
    const before = chunkStarts.filter((s) => s < state.position - 2);
    transport.seek(before.length ? before[before.length - 1]! : 0);
  }
  function next() {
    const after = chunkStarts.find((s) => s > state.position + 0.05);
    if (after !== undefined) transport.seek(after);
  }

  return (
    <div className="transport" role="region" aria-label={`Episode playback${title ? ` — ${title}` : ""}`}>
      <div className="transport-who">
        <span className="icon-tile sz-44" aria-hidden>
          🎙️
        </span>
        <span className="transport-who-text">
          <span title={title}>{title}</span>
          <span>{subtitle}</span>
        </span>
      </div>

      <div className="transport-keys">
        <button type="button" className="round" aria-label="Previous chunk" onClick={previous}>
          <PrevIcon />
        </button>
        <button
          type="button"
          className="transport-play"
          onClick={() => transport.toggle()}
          aria-label={state.playing ? "Pause" : "Play the whole episode"}
          title={state.playing ? "Pause (space)" : "Play the whole episode (space)"}
        >
          {state.playing ? <PauseIcon size={16} /> : <PlayIcon size={16} />}
        </button>
        <button
          type="button"
          className="round"
          aria-label="Next chunk"
          onClick={next}
          disabled={!chunkStarts.some((s) => s > state.position + 0.05)}
        >
          <NextIcon />
        </button>
      </div>

      <div className="transport-time">
        <span className="mono">{now}</span>
        <div className="transport-scrub">
          <div className="player-track" />
          <div className="player-fill" style={{ width: `${pct}%` }} />
          <div className="player-head" style={{ left: `${pct}%` }} />
          {state.duration > 0 &&
            markers.map((marker, index) => (
              <span
                key={index}
                aria-hidden
                className={[
                  "transport-dot",
                  marker.planned ? "planned" : "",
                  state.muted.effect ? "muted" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={{ left: `${Math.min(100, (marker.at / state.duration) * 100)}%` }}
              />
            ))}
          {/* The real control, laid over the drawing: a range input, so the
              position can be moved by keyboard as well as by pointer. */}
          <input
            type="range"
            className="transport-range"
            aria-label="Playback position"
            aria-valuetext={`${now} of ${total}`}
            min={0}
            max={state.duration || 0}
            step={0.1}
            value={Math.min(state.position, state.duration || 0)}
            onChange={(event) => transport.seek(Number(event.target.value))}
          />
        </div>
        <span className="mono faint">{total}</span>
      </div>

      <div className="transport-lanes">
        <LaneToggle lane="narration" muted={state.muted.narration} label="Narration" icon="🎙️" />
        <LaneToggle lane="effect" muted={state.muted.effect} label="Effects" icon="✨" />
        <span className="transport-hint" title="Space plays and pauses">
          ␣ space
        </span>
      </div>
    </div>
  );
}

function LaneToggle({
  lane,
  muted,
  label,
  icon,
}: {
  lane: "narration" | "effect";
  muted: boolean;
  label: string;
  icon: string;
}) {
  return (
    <button
      type="button"
      className="chip"
      aria-pressed={!muted}
      onClick={() => transport.setMuted(lane, !muted)}
      title={`${muted ? "Unmute" : "Mute"} ${label.toLowerCase()}`}
    >
      <span aria-hidden>{icon}</span>
      {label}
    </button>
  );
}

/** Subscribe a component to the transport's clock. */
export function useTransport(): TransportState {
  const [state, setState] = useState<TransportState>(() => ({
    playing: false,
    position: 0,
    duration: 0,
    muted: { narration: false, effect: false },
    ready: false,
  }));
  useEffect(() => transport.subscribe(setState), []);
  return state;
}
