import type { Timeline, TimelineEntry } from "../types";
import { api } from "../api";
import { Player } from "./Player";

/** The running order. Effects share a start time with the narration they sit
 *  over, because they are overlays — that is deliberate, not a rendering bug,
 *  so the table says so. */
export function TimelineView({ timeline }: { timeline: Timeline | null }) {
  if (!timeline) return <p className="faint">No script selected.</p>;
  if (!timeline.entries.length) {
    return (
      <div className="empty">
        <strong>Nothing on the timeline</strong>
        This script has no chunks yet.
      </div>
    );
  }

  const drifted = timeline.entries.filter((e) => e.drift_s !== null && e.drift_s !== 0);

  return (
    <>
      <table>
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Start</th>
            <th>End</th>
            <th className="num">Length</th>
            <th>Track</th>
            <th>Content</th>
            <th>Audio</th>
          </tr>
        </thead>
        <tbody>
          {timeline.entries.map((entry) => (
            <Row key={entry.index} entry={entry} />
          ))}
        </tbody>
      </table>

      {drifted.length > 0 && (
        <div className="note">
          <strong className="warn">Anchor drift</strong>
          <ul>
            {drifted.map((e) => (
              <li key={e.index}>
                Chunk {e.chunk_ordinal} targeted {fmt(e.target_s ?? 0)}, landed {e.start} (
                {(e.drift_s ?? 0) > 0 ? "+" : ""}
                {(e.drift_s ?? 0).toFixed(1)}s)
              </li>
            ))}
          </ul>
        </div>
      )}

      {!timeline.complete && (
        <p className="note warn">
          Timings after the first ungenerated chunk are provisional.
        </p>
      )}
    </>
  );
}

function Row({ entry }: { entry: TimelineEntry }) {
  const src =
    entry.kind === "narration" && entry.take_id
      ? api.audioTake(entry.take_id)
      : entry.effect_id
        ? api.audioEffect(entry.effect_id)
        : null;

  return (
    <tr>
      <td className="num dim">{entry.index}</td>
      <td className="mono">{entry.start}</td>
      <td className="mono">{entry.end}</td>
      <td className="num mono">{entry.duration_s.toFixed(2)}s</td>
      <td>
        <span className={`tag ${entry.kind === "narration" ? "" : entry.kind}`}>
          {entry.kind === "planned" ? "planned" : entry.kind}
        </span>
      </td>
      <td className={`kind-${entry.kind}`}>
        {entry.kind === "narration" ? entry.label : <em>{entry.label}</em>}
      </td>
      <td>
        {src ? (
          <Player src={src} duration={entry.duration_s} />
        ) : (
          <span className="faint">—</span>
        )}
      </td>
    </tr>
  );
}

function fmt(seconds: number): string {
  const whole = Math.floor(seconds);
  const ms = Math.round((seconds - whole) * 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(Math.floor(whole / 3600))}:${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}.${pad(ms, 3)}`;
}
