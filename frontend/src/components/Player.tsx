import { useEffect, useRef, useState } from "react";
import { claimSolo, releaseSolo } from "../audioBus";
import { DownloadIcon, PauseIcon, PlayIcon } from "./Icon";

/** The one currently playing, so starting a second stops the first.
 *
 *  With seven players on a screen, overlapping audio is otherwise the default
 *  and auditioning takes becomes genuinely hard. The transport is the deliberate
 *  exception — it plays several at once — so both go through `audioBus`, which
 *  keeps the "only one source is audible" rule in one place. */
let current: HTMLAudioElement | null = null;

/** Anything that wants to follow the active playhead — the track view does. */
type Listener = (src: string | null, seconds: number) => void;
const listeners = new Set<Listener>();

export function onPlayhead(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function announce(src: string | null, seconds: number) {
  for (const listener of listeners) listener(src, seconds);
}

/** A compact one-line player: play/pause, a seekable bar, and the time.
 *
 *  Replaces the native <audio controls>, which is chunky, differs between
 *  browsers, and cannot be styled to match anything. */
export function Player({
  src,
  duration,
  download,
  wide,
  cue,
}: {
  src: string;
  /** Known ahead of metadata load, so the bar is right before first play. */
  duration?: number | null;
  download?: string;
  wide?: boolean;
  /** Jump to `at` seconds and play. A new `nonce` repeats the jump, so the same
   *  spot can be played twice in a row. */
  cue?: { at: number; nonce: number } | null;
}) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const scrub = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  // A stable identity for the bus, so it can silence exactly this player.
  const pause = useRef(() => {
    ref.current?.pause();
    setPlaying(false);
  }).current;
  const [at, setAt] = useState(0);
  const [total, setTotal] = useState(duration ?? 0);

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "metadata";
    audio.src = src;
    ref.current = audio;

    const onTime = () => {
      setAt(audio.currentTime);
      announce(src, audio.currentTime);
    };
    const onMeta = () => {
      if (Number.isFinite(audio.duration)) setTotal(audio.duration);
    };
    const onEnd = () => {
      setPlaying(false);
      setAt(0);
      announce(null, 0);
    };
    const onPause = () => setPlaying(false);

    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnd);
    audio.addEventListener("pause", onPause);

    return () => {
      audio.pause();
      releaseSolo(pause);
      if (current === audio) {
        current = null;
        announce(null, 0);
      }
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnd);
      audio.removeEventListener("pause", onPause);
      audio.src = "";
      ref.current = null;
    };
  }, [src, pause]);

  // Playing from a cue goes through the same solo rule as the play button, so
  // hearing a flagged word never overlaps whatever else was playing.
  useEffect(() => {
    const audio = ref.current;
    if (!cue || !audio) return;
    if (current && current !== audio) current.pause();
    current = audio;
    claimSolo(pause);
    audio.currentTime = Math.max(0, cue.at);
    setAt(audio.currentTime);
    void audio.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  }, [cue, pause]);

  function toggle() {
    const audio = ref.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      releaseSolo(pause);
      return;
    }
    if (current && current !== audio) current.pause();
    current = audio;
    // Silences the transport, and any other solo, before this one starts.
    claimSolo(pause);
    void audio.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  }

  function seek(event: React.MouseEvent) {
    const audio = ref.current;
    const box = scrub.current?.getBoundingClientRect();
    if (!audio || !box || !total) return;
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    audio.currentTime = ratio * total;
    setAt(audio.currentTime);
  }

  const pct = total ? Math.min(100, (at / total) * 100) : 0;

  return (
    <div className={`player${wide ? " wide" : ""}${playing ? " active" : ""}`}>
      <button
        className={`player-btn${playing ? " playing" : ""}`}
        onClick={toggle}
        aria-label={playing ? "Pause" : "Play"}
        title={playing ? "Pause" : "Play"}
      >
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>

      <div className="player-scrub" ref={scrub} onClick={seek}>
        <div className="player-track" />
        <div className="player-fill" style={{ width: `${pct}%` }} />
        <div className="player-head" style={{ left: `${pct}%` }} />
      </div>

      <span className="player-time">
        {clock(at)}
        {total ? ` / ${clock(total)}` : ""}
      </span>

      {download && (
        <a className="download" href={download} download title="Download">
          <DownloadIcon />
        </a>
      )}
    </div>
  );
}

function clock(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
