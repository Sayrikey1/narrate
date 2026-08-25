import { useEffect, useRef, useState } from "react";
import { formatClock } from "../time";
import * as transport from "../transport";
import type { TransportState } from "../transport";
import { PauseIcon, PlayIcon, SparkIcon, WaveIcon } from "./Icon";

/** The persistent transport bar.
 *
 *  Lives in the shell rather than on a page, so playback survives navigation —
 *  looking at the cost page should not stop the episode. */
export function Transport({ title }: { title?: string }) {
  const state = useTransport();
  const scrub = useRef<HTMLDivElement | null>(null);

  // Space plays and pauses, unless something is being typed into.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (target?.isContentEditable) return;
      event.preventDefault();
      transport.toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!state.ready) return null;

  const pct = state.duration ? (state.position / state.duration) * 100 : 0;

  return (
    <div className="transport">
      <button
        className={`player-btn transport-play${state.playing ? " playing" : ""}`}
        onClick={() => transport.toggle()}
        aria-label={state.playing ? "Pause" : "Play the whole timeline"}
        title={state.playing ? "Pause (space)" : "Play the whole timeline (space)"}
      >
        {state.playing ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
      </button>

      <div
        className="player-scrub transport-scrub"
        ref={scrub}
        onClick={(event) => {
          const box = scrub.current?.getBoundingClientRect();
          if (!box || !state.duration) return;
          const ratio = (event.clientX - box.left) / box.width;
          transport.seek(Math.min(1, Math.max(0, ratio)) * state.duration);
        }}
      >
        <div className="player-track" />
        <div className="player-fill" style={{ width: `${pct}%` }} />
        <div className="player-head" style={{ left: `${pct}%` }} />
      </div>

      <span className="player-time">
        {formatClock(state.position)} / {formatClock(state.duration)}
      </span>

      <div className="transport-lanes">
        <LaneToggle lane="narration" muted={state.muted.narration} label="Narration">
          <WaveIcon />
        </LaneToggle>
        <LaneToggle lane="effect" muted={state.muted.effect} label="Effects">
          <SparkIcon />
        </LaneToggle>
      </div>

      {title && <span className="transport-title faint">{title}</span>}
    </div>
  );
}

function LaneToggle({
  lane,
  muted,
  label,
  children,
}: {
  lane: "narration" | "effect";
  muted: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`chip${muted ? "" : " on"}`}
      onClick={() => transport.setMuted(lane, !muted)}
      title={`${muted ? "Unmute" : "Mute"} ${label.toLowerCase()}`}
    >
      {children}
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
