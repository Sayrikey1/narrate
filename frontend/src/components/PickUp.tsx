import { Link, scriptPath } from "../router";
import * as transport from "../transport";
import type { ChunkRow, ScriptSummary, Timeline } from "../types";
import { PauseIcon, PlayIcon } from "./Icon";
import { useTransport } from "./Transport";
import { usePeaks, WaveBars } from "./Wave";

/** The episode last worked on, and what is in its way — so opening the app
 *  starts with the next thing to do rather than a list to search. */
export function PickUp({
  episode,
  timeline,
  chunks,
  lastActivity,
}: {
  episode: ScriptSummary;
  timeline: Timeline | null;
  chunks: ChunkRow[];
  /** When anything last happened to it, from its records. */
  lastActivity?: string;
}) {
  const player = useTransport();
  const wave = usePeaks(`/api/scripts/${episode.id}/waveform?n=44`);
  const inCut = chunks.flatMap((c) => c.takes.filter((t) => t.in_cut && !t.verify_accepted));
  const suspect = inCut.filter((t) => t.verify_status === "suspect").length;
  const review = inCut.filter((t) => t.verify_status === "review").length;
  const unchecked = inCut.filter(
    (t) => t.verify_status === "unverified" || t.verify_status === "error",
  ).length;
  const missing = (timeline?.entries ?? [])
    .filter((e) => e.kind === "narration" && !e.generated)
    .map((e) => e.chunk_ordinal ?? 0);
  const complete = timeline !== null && chunks.length > 0 && missing.length === 0;
  const workbench = scriptPath(episode.id);

  const facts = [
    timeline && timeline.runtime_s > 0
      ? `${clock(timeline.runtime_s)}${complete ? "" : " so far"}`
      : null,
    `${episode.chunks} chunk${episode.chunks === 1 ? "" : "s"}`,
    `${episode.chars.toLocaleString()} chars`,
    lastActivity ? `last worked on ${workedOn(lastActivity)}` : null,
  ].filter(Boolean);

  return (
    <section className="panel pickup" aria-labelledby="pickup-h">
      {wave && wave.some((p) => p > 0) && (
        <WaveBars
          peaks={wave}
          played={player.duration ? player.position / player.duration : 0}
          className="pickup-wave"
        />
      )}
      <span className="pickup-kicker">⏯️ Pick up where you left off</span>
      <div className="pickup-title">
        <span className="icon-tile sz-70" aria-hidden>
          🎙️
        </span>
        <div className="pickup-text">
          <h2 id="pickup-h">{episode.title}</h2>
          <span>{facts.join(" · ")}</span>
        </div>
      </div>

      {timeline && (
        <div className="pills">
          {suspect > 0 && (
            <span className="pill bad">
              🚩 {suspect} take{suspect === 1 ? "" : "s"} flagged
            </span>
          )}
          {review > 0 && <span className="pill warn">👀 {review} worth a listen</span>}
          {missing.length > 0 && <span className="pill warn">🕳️ {holes(missing)}</span>}
          {unchecked > 0 && missing.length === 0 && (
            <span className="pill quiet">
              🔍 {unchecked} take{unchecked === 1 ? "" : "s"} not checked yet
            </span>
          )}
          {complete && suspect + review + unchecked === 0 && (
            <span className="pill ok">✅ Every chunk has audio, and every take checks out</span>
          )}
        </div>
      )}

      <div className="toolbar">
        {suspect + review > 0 ? (
          <>
            <Link className="btn primary large" to={`${workbench}#fix`}>
              Fix what's flagged…
            </Link>
            <Link className="btn large" to={workbench}>
              Open the workbench →
            </Link>
          </>
        ) : missing.length > 0 ? (
          <>
            <Link className="btn primary large" to={`${workbench}#generate`}>
              Generate what's missing…
            </Link>
            <Link className="btn large" to={workbench}>
              Open the workbench →
            </Link>
          </>
        ) : (
          <Link className="btn primary large" to={workbench}>
            Open the workbench →
          </Link>
        )}
        {player.duration > 0 && (
          <button type="button" className="ghost large" onClick={() => transport.toggle()}>
            {player.playing ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
            {player.playing ? `Pause at ${clock(player.position)}` : `Play from ${clock(player.position)}`}
          </button>
        )}
      </div>
    </section>
  );
}

/** "Chunk 8 has no audio, so export is waiting on it". */
function holes(missing: number[]): string {
  if (missing.length === 1) return `Chunk ${missing[0]} has no audio, so export is waiting on it`;
  const named =
    missing.length <= 3
      ? `Chunks ${missing.slice(0, -1).join(", ")} and ${missing[missing.length - 1]}`
      : `${missing.length} chunks`;
  return `${named} have no audio, so export is waiting on them`;
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** "today", "yesterday", or "Fri 23 Oct". */
function workedOn(iso: string): string {
  const at = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (at.toDateString() === today.toDateString()) return "today";
  if (at.toDateString() === yesterday.toDateString()) return "yesterday";
  return at.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}
